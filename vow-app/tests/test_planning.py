"""Tests for the harness planning loop (plan -> act -> observe).

What Step 23 added to AgentHarness:
  - a one-call planning phase before the tool loop (`_make_plan`)
  - a harness-owned update_plan tool: mark steps done / replan with a reason
  - `plan::{json}` events streamed through on_event for the UI checklist
  - the tool loop survives a crashing tool (the model observes the error)

No network calls: every test scripts a fake OpenAI client.
"""

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

VOW_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VOW_APP))
os.environ.setdefault("VOW_DATA_DIR", tempfile.mkdtemp(prefix="vow-test-data-"))
os.environ["VOW_STORAGE_BACKEND"] = "files"

from agent.harness import AgentHarness, extract_json  # noqa: E402


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------
def _resp(content=None, tool_calls=None, completion_tokens=50):
    message = types.SimpleNamespace(content=content, tool_calls=tool_calls)
    usage = types.SimpleNamespace(
        prompt_tokens=100, completion_tokens=completion_tokens,
        total_tokens=100 + completion_tokens,
    )
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=message)], usage=usage)


def _tool_call(call_id, name, args: dict):
    fn = types.SimpleNamespace(name=name, arguments=json.dumps(args))
    return types.SimpleNamespace(id=call_id, function=fn)


class _ScriptedClient:
    """Returns the given responses in order; records every request payload."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.script.pop(0)


def _harness(script, **kwargs):
    events = []
    h = AgentHarness(
        verbose=False, on_event=events.append,
        run_log_path=tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name,
        **kwargs,
    )
    h.client = _ScriptedClient(script)
    return h, events


def _plan_events(events):
    return [json.loads(e.split("plan::", 1)[1])
            for e in events if e.startswith("plan::")]


PLAN_JSON = '{"plan": ["Read the skill", "Read the data", "Analyze"]}'


# ---------------------------------------------------------------------------
# the planning phase
# ---------------------------------------------------------------------------
class TestPlanningPhase(unittest.TestCase):
    def test_plan_created_emitted_and_owned_by_the_model(self):
        h, events = _harness([_resp(content=PLAN_JSON), _resp(content="done")])
        answer = h.run("task")
        self.assertEqual(answer, "done")
        plans = _plan_events(events)
        self.assertGreaterEqual(len(plans), 1)
        first = plans[0]["steps"]
        self.assertEqual([s["text"] for s in first],
                         ["Read the skill", "Read the data", "Analyze"])
        self.assertEqual(first[0]["status"], "active")
        # The plan is injected into history as the model's own words.
        self.assertTrue(any(
            m.get("role") == "assistant" and "My plan:" in (m.get("content") or "")
            for m in h.chat_history if isinstance(m, dict)))

    def test_final_answer_closes_out_the_plan(self):
        h, events = _harness([_resp(content=PLAN_JSON), _resp(content="done")])
        h.run("task")
        last = _plan_events(events)[-1]
        self.assertTrue(all(s["status"] == "done" for s in last["steps"]))

    def test_unparseable_plan_never_blocks_the_run(self):
        h, events = _harness([_resp(content="I cannot plan, sorry"),
                              _resp(content="answer anyway")])
        self.assertEqual(h.run("task"), "answer anyway")
        self.assertEqual(_plan_events(events), [])
        self.assertEqual(h.plan, [])

    def test_planning_disabled_skips_the_extra_call(self):
        h, events = _harness([_resp(content="direct answer")],
                             enable_planning=False)
        self.assertEqual(h.run("task"), "direct answer")
        self.assertEqual(len(h.client.requests), 1)  # loop call only
        self.assertEqual(_plan_events(events), [])

    def test_update_plan_schema_only_offered_when_a_plan_exists(self):
        h, _ = _harness([_resp(content=PLAN_JSON), _resp(content="done")])
        h.run("task")
        loop_request = h.client.requests[-1]
        names = [t["function"]["name"] for t in loop_request["tools"]]
        self.assertIn("update_plan", names)

        h2, _ = _harness([_resp(content="no json"), _resp(content="done")])
        h2.run("task")
        names2 = [t["function"]["name"] for t in h2.client.requests[-1]["tools"]]
        self.assertNotIn("update_plan", names2)


# ---------------------------------------------------------------------------
# update_plan: progress + replanning
# ---------------------------------------------------------------------------
class TestUpdatePlan(unittest.TestCase):
    def test_marks_steps_done_and_advances(self):
        h, events = _harness([
            _resp(content=PLAN_JSON),
            _resp(tool_calls=[
                _tool_call("1", "list_skills", {}),
                _tool_call("2", "update_plan",
                           {"completed_steps": [1], "current_step": 2}),
            ]),
            _resp(content="done"),
        ])
        h.run("task")
        mid = _plan_events(events)[1]["steps"]
        self.assertEqual(mid[0]["status"], "done")
        self.assertEqual(mid[1]["status"], "active")

    def test_replan_keeps_done_steps_and_reports_the_reason(self):
        h, events = _harness([
            _resp(content=PLAN_JSON),
            _resp(tool_calls=[_tool_call(
                "1", "update_plan", {"completed_steps": [1]})]),
            _resp(tool_calls=[_tool_call("2", "update_plan", {
                "revised_plan": ["Read the skill", "Ask the couple", "Analyze"],
                "reason": "data file is empty",
                "current_step": 2,
            })]),
            _resp(content="done"),
        ])
        h.run("task")
        replanned = _plan_events(events)[2]
        self.assertEqual(replanned.get("reason"), "data file is empty")
        steps = replanned["steps"]
        self.assertEqual(steps[0]["status"], "done")       # survived the replan
        self.assertEqual(steps[1]["text"], "Ask the couple")
        self.assertEqual(steps[1]["status"], "active")

    def test_update_plan_never_reaches_the_tool_registry(self):
        h, _ = _harness([
            _resp(content=PLAN_JSON),
            _resp(tool_calls=[_tool_call("1", "update_plan", {"current_step": 1})]),
            _resp(content="done"),
        ])
        h.tools.execute_tool = lambda *a, **k: self.fail(
            "update_plan must be handled by the harness, not the registry")
        self.assertEqual(h.run("task"), "done")


# ---------------------------------------------------------------------------
# observe: a crashing tool must not kill the run
# ---------------------------------------------------------------------------
class TestToolCrashSurvival(unittest.TestCase):
    def test_loop_survives_and_model_sees_the_error(self):
        h, events = _harness([
            _resp(content=PLAN_JSON),
            _resp(tool_calls=[_tool_call("1", "read_data", {"name": "budget"})]),
            _resp(content="recovered"),
        ])
        def boom(*a, **k):
            raise RuntimeError("disk on fire")
        h.tools.execute_tool = boom
        self.assertEqual(h.run("task"), "recovered")
        # The error was fed back as a tool result for the model to observe.
        tool_msgs = [m for m in h.chat_history
                     if isinstance(m, dict) and m.get("role") == "tool"]
        self.assertTrue(any("disk on fire" in m["content"] for m in tool_msgs))
        self.assertTrue(any("failed — rethinking" in e for e in events))


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------
class TestExtractJson(unittest.TestCase):
    def test_direct_fenced_and_embedded(self):
        want = {"plan": ["a"]}
        self.assertEqual(extract_json('{"plan": ["a"]}'), want)
        self.assertEqual(extract_json('```json\n{"plan": ["a"]}\n```'), want)
        self.assertEqual(extract_json('Sure!\n{"plan": ["a"]}\nDone.'), want)
        self.assertIsNone(extract_json("no json here"))


if __name__ == "__main__":
    unittest.main()
