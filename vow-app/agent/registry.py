import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

BASE = Path(__file__).resolve().parent.parent
SKILLS_DIR = BASE / "skills"
# Must match the server's data dir (see app/core.py) so the agent reads/writes
# the same files the web app does. Overridable via VOW_DATA_DIR.
DATA_DIR = Path(os.environ.get("VOW_DATA_DIR", BASE / "data"))
# Timestamped copies are written here before every overwrite, so a bad or
# injected write is always reversible.
BACKUP_DIR = DATA_DIR / ".backups"
KEEP_BACKUPS = 10  # per dataset

# Guardrail: the agent may only read/write these datasets.
ALLOWED_DATA = {"budget", "vendors", "guests", "contracts", "decisions"}


class ToolRegistry:
    def __init__(self):
        self.registry = {}
        self.init_tools()

    def init_tools(self):
        self.register_tool(
            "list_skills",
            "List available skills (name + description). Call this first for any task.",
            {"type": "object", "properties": {}, "required": []},
            self._list_skills,
        )
        self.register_tool(
            "read_skill",
            "Read a skill's full instructions plus lessons learned from past runs.",
            {"type": "object",
             "properties": {"name": {"type": "string", "description": "Skill name."}},
             "required": ["name"]},
            self._read_skill,
        )
        self.register_tool(
            "read_data",
            f"Read a wedding dataset. One of: {sorted(ALLOWED_DATA)}.",
            {"type": "object",
             "properties": {"name": {"type": "string"}},
             "required": ["name"]},
            self._read_data,
        )
        self.register_tool(
            "write_data",
            "Overwrite a wedding dataset with new JSON content.",
            {"type": "object",
             "properties": {"name": {"type": "string"},
                            "content": {"type": "string", "description": "JSON string."}},
             "required": ["name", "content"]},
            self._write_data,
        )
        self.register_tool(
            "append_lesson",
            "Record a short reusable lesson into a skill's memory for future runs.",
            {"type": "object",
             "properties": {"skill": {"type": "string"}, "lesson": {"type": "string"}},
             "required": ["skill", "lesson"]},
            self._append_lesson,
        )

    # --- skills ---

    @staticmethod
    def _frontmatter(text: str) -> dict:
        meta = {}
        if text.startswith("---"):
            for line in text.split("---")[1].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
        return meta

    def _list_skills(self):
        skills = []
        for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
            meta = self._frontmatter(path.read_text())
            skills.append({"name": path.parent.name,
                           "description": meta.get("description", "")})
        return skills or {"note": "No skills installed yet."}

    def _read_skill(self, name: str):
        skill_dir = SKILLS_DIR / name
        if not skill_dir.is_dir():
            return {"error": f"No skill named '{name}'."}
        out = (skill_dir / "SKILL.md").read_text()
        lessons = skill_dir / "LESSONS.md"
        if lessons.exists() and lessons.read_text().strip():
            out += "\n\n# Lessons learned from past runs\n" + lessons.read_text()
        return out

    # --- data (whitelisted) ---

    def _read_data(self, name: str):
        if name not in ALLOWED_DATA:
            return {"error": f"Unknown dataset '{name}'.", "allowed": sorted(ALLOWED_DATA)}
        path = DATA_DIR / f"{name}.json"
        if not path.exists():
            return {"note": f"'{name}' has no data yet."}
        return json.loads(path.read_text())

    @staticmethod
    def _is_empty(value) -> bool:
        """True for the 'blanked' shapes: None, {}, [], "" ."""
        if value is None:
            return True
        if isinstance(value, (dict, list, str)) and len(value) == 0:
            return True
        return False

    def _backup(self, name: str, path: Path):
        """Copy the current file to .backups/<name>.<timestamp>.json before it
        is overwritten, and prune to the most recent KEEP_BACKUPS."""
        if not path.exists():
            return
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        (BACKUP_DIR / f"{name}.{stamp}.json").write_text(path.read_text())
        backups = sorted(BACKUP_DIR.glob(f"{name}.*.json"))
        for old in backups[:-KEEP_BACKUPS]:
            old.unlink()

    def _write_data(self, name: str, content: str):
        if name not in ALLOWED_DATA:
            return {"error": f"Writing '{name}' is not allowed.", "allowed": sorted(ALLOWED_DATA)}
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON: {e}"}

        path = DATA_DIR / f"{name}.json"

        # Destructive-write guard: refuse to blank out or change the shape of an
        # existing, non-empty dataset. A legitimate edit keeps the same top-level
        # type and still has content; blanking/retyping signals a bug or an
        # injected "wipe the data" instruction.
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                existing = None
            if existing is not None and not self._is_empty(existing):
                if self._is_empty(parsed):
                    return {"error": (
                        f"Refused: this would erase all '{name}' data. Deleting "
                        f"everything must be done explicitly by the couple, not via "
                        f"write_data.")}
                if type(parsed) is not type(existing):
                    return {"error": (
                        f"Refused: '{name}' is a {type(existing).__name__} but the new "
                        f"content is a {type(parsed).__name__}. This looks like "
                        f"corruption, not an edit.")}

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._backup(name, path)
        path.write_text(json.dumps(parsed, indent=2))
        return {"ok": True}

    # --- self-improvement ---

    def _append_lesson(self, skill: str, lesson: str):
        skill_dir = SKILLS_DIR / skill
        if not skill_dir.is_dir():
            return {"error": f"No skill named '{skill}'."}
        lessons = skill_dir / "LESSONS.md"
        existing = lessons.read_text() if lessons.exists() else ""
        if lesson.strip() in existing:
            return {"ok": True, "note": "Lesson already recorded."}
        with lessons.open("a") as f:
            f.write(f"- {lesson.strip()}\n")
        return {"ok": True}

    # --- unchanged from the workshop version ---

    def register_tool(self, name: str, description: str, parameters: dict, function: Callable):
        self.registry[name] = {
            "schema": {"type": "function",
                       "function": {"name": name, "description": description,
                                    "parameters": parameters}},
            "execute": function,
        }

    def get_tool_schemas(self):
        return [tool["schema"] for tool in self.registry.values()]

    def execute_tool(self, name: str, parameters: str) -> Any:
        args = json.loads(parameters) if isinstance(parameters, str) else parameters
        return self.registry[name]["execute"](**args)
