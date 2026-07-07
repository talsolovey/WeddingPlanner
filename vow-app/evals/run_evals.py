"""Scored eval harness for Vow's specialist skills.

Each case in evals/cases/<skill>.json plants known traps into fixture datasets
and lists, per trap, the keywords that count as "the agent caught it". The
runner seeds the fixtures under a throwaway eval couple, runs the REAL
production path (the orchestrator's specialist prompt + a live AgentHarness),
and scores:

  recall  — planted traps the findings mention (the number that matters)
  noise   — findings that match no trap (verbosity / hallucination signal)
  cost    — real dollars for the run

Results are written to evals/results/<skill>-<stamp>.json so scores are
comparable across time (and across lessons on/off).

USAGE (from vow-app/, needs OPENAI_API_KEY for live runs):
  python -m evals.run_evals                     # all skills, lessons ON
  python -m evals.run_evals --skill budget-forecaster
  python -m evals.run_evals --no-lessons        # LESSONS.md hidden for the run
  python -m evals.run_evals --compare-lessons   # both, prints the delta
  python -m evals.run_evals --dry-run           # validate cases, no model calls
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

VOW_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VOW_APP))

# Evals seed throwaway fixtures — they must never write into the production
# database, even when .env holds live Supabase credentials.
os.environ["VOW_STORAGE_BACKEND"] = "files"

import storage  # noqa: E402
from agent.orchestrator import (  # noqa: E402
    FINDINGS_SCHEMA, SPECIALIST_PROMPT, _extract_json,
)
from agent.registry import SKILLS_DIR  # noqa: E402

CASES_DIR = Path(__file__).resolve().parent / "cases"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
EVAL_COUPLE = "eval-fixture"
TODAY = "2026-07-07"  # frozen so date-sensitive traps (overdue etc.) stay valid


# ---------- scoring (pure, unit-tested offline) ----------

def score_findings(findings: list, traps: list) -> dict:
    """Keyword recall: a trap is HIT if any of its keywords appears in any
    finding; a finding is NOISE if it matches no trap at all."""
    texts = [json.dumps(f, ensure_ascii=False).lower() for f in findings
             if isinstance(f, dict)]
    blob = " ".join(texts)
    hits, misses = [], []
    for trap in traps:
        if any(k.lower() in blob for k in trap["any_of"]):
            hits.append(trap["id"])
        else:
            misses.append(trap["id"])
    noise = 0
    all_keywords = [k.lower() for t in traps for k in t["any_of"]]
    for text in texts:
        if not any(k in text for k in all_keywords):
            noise += 1
    return {
        "recall": round(len(hits) / len(traps), 3) if traps else None,
        "hits": hits, "misses": misses,
        "findings_count": len(texts), "noise_count": noise,
    }


def load_cases(skill: str = None) -> list:
    cases = []
    for path in sorted(CASES_DIR.glob("*.json")):
        case = json.loads(path.read_text())
        if skill and case["skill"] != skill:
            continue
        case["_file"] = path.name
        cases.append(case)
    return cases


def validate_case(case: dict) -> list:
    problems = []
    for key in ("skill", "specialist", "datasets", "traps"):
        if key not in case:
            problems.append(f"missing '{key}'")
    if not (SKILLS_DIR / case.get("skill", "")).is_dir():
        problems.append(f"skill '{case.get('skill')}' not installed")
    for trap in case.get("traps", []):
        if not trap.get("any_of"):
            problems.append(f"trap '{trap.get('id')}' has no keywords")
    return problems


# ---------- lessons toggle ----------

class lessons_hidden:
    """Temporarily hide every skill's LESSONS.md (for the lessons-off arm)."""

    def __enter__(self):
        self.moved = []
        for lessons in SKILLS_DIR.glob("*/LESSONS.md"):
            aside = lessons.with_suffix(".md.evalhidden")
            lessons.rename(aside)
            self.moved.append((lessons, aside))
        return self

    def __exit__(self, *exc):
        for lessons, aside in self.moved:
            if aside.exists():
                aside.rename(lessons)
        return False


# ---------- live run ----------

def run_case(case: dict, harness_factory=None) -> dict:
    """Seed fixtures under the eval couple, run the production specialist
    prompt through a real harness, score the findings."""
    storage.set_couple(EVAL_COUPLE)
    try:
        for name, data in case["datasets"].items():
            storage.save(name, data)
        if harness_factory is None:
            from agent.harness import AgentHarness
            harness = AgentHarness(max_cost_usd=0.15, verbose=False,
                                   run_log_path=str(VOW_APP / "logs" / "run_log.jsonl"))
        else:
            harness = harness_factory()
        prompt = SPECIALIST_PROMPT.format(
            today=TODAY, name=case["specialist"], skill=case["skill"],
            datasets=", ".join(f'"{d}"' for d in case["datasets"]),
            schema=FINDINGS_SCHEMA)
        answer = harness.run(prompt)
    finally:
        storage.set_couple(None)
    parsed = _extract_json(answer) or {}
    findings = parsed.get("findings") or []
    result = score_findings(findings, case["traps"])
    result.update({
        "skill": case["skill"],
        "cost_usd": round(getattr(harness, "last_run_cost", 0.0), 4),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "findings": findings,
    })
    return result


def save_result(result: dict, lessons_enabled: bool):
    RESULTS_DIR.mkdir(exist_ok=True)
    result = dict(result, lessons_enabled=lessons_enabled)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"{result['skill']}-{stamp}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return path


def print_row(result: dict, label: str = ""):
    print(f"  {result['skill']:<22} recall {len(result['hits'])}/"
          f"{len(result['hits']) + len(result['misses'])}"
          f"  noise {result['noise_count']}"
          f"  ${result['cost_usd']:.3f} {label}")
    if result["misses"]:
        print(f"    missed: {', '.join(result['misses'])}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skill", help="run one skill's case only")
    ap.add_argument("--no-lessons", action="store_true")
    ap.add_argument("--compare-lessons", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cases = load_cases(args.skill)
    if not cases:
        sys.exit(f"No cases found{' for ' + args.skill if args.skill else ''}.")

    if args.dry_run:
        ok = True
        for case in cases:
            problems = validate_case(case)
            status = "OK" if not problems else "; ".join(problems)
            print(f"  {case['_file']:<28} {len(case['traps'])} traps  {status}")
            ok = ok and not problems
        sys.exit(0 if ok else 1)

    def run_all(lessons_enabled: bool):
        results = []
        for case in cases:
            result = run_case(case)
            save_result(result, lessons_enabled)
            print_row(result, "" if lessons_enabled else "(lessons OFF)")
            results.append(result)
        return results

    if args.compare_lessons:
        print("lessons ON:")
        with_lessons = run_all(True)
        print("lessons OFF:")
        with lessons_hidden():
            without = run_all(False)
        print("\nlessons effect (recall on - off):")
        for a, b in zip(with_lessons, without):
            delta = len(a["hits"]) - len(b["hits"])
            print(f"  {a['skill']:<22} {delta:+d}")
    elif args.no_lessons:
        with lessons_hidden():
            run_all(False)
    else:
        run_all(True)


if __name__ == "__main__":
    main()
