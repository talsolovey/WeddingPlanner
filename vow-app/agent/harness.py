import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

import openai
from dotenv import load_dotenv

from .context_manager import ContextManager
from .registry import ToolRegistry

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")

SYSTEM_PROMPT = """You are Vow, an AI wedding-planning agent helping a couple plan their wedding.

How you work:
1. For every task, first call list_skills, then read_skill for the most relevant skill, \
and follow its instructions. Skills contain hard-won domain knowledge — do not improvise \
where a skill exists.
2. Investigate before judging: read the relevant data, weigh trade-offs, and explain your \
reasoning in your final answer.
3. If you learn something reusable (a pitfall, a better method, a surprising fact), record \
it with append_lesson on the relevant skill.
4. Be honest about uncertainty. Flag anything that needs the couple's attention or a human \
decision.
"""


class AgentHarness:
    # USD per 1M tokens (input, output). Update to match current OpenAI pricing.
    PRICING = {
        "gpt-4o": {"prompt": 2.50, "completion": 10.00},
    }

    def __init__(
        self,
        model: str = "gpt-4o",
        max_iterations: int = 10,
        max_context_tokens: int = 6000,
        run_log_path: str = str(BASE / "logs" / "run_log.jsonl"),
        verbose: bool = True,
    ):
        self.model = model
        self.client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.max_iterations = max_iterations
        self.tools = ToolRegistry()
        self.chat_history: List[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.context_manager = ContextManager(
            max_context_tokens=max_context_tokens, client=self.client, model=model
        )
        # Real token accounting from the API, plus a per-call run log on disk.
        self.token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.cost_usd = 0.0
        self.last_run_cost = 0.0
        self.run_log_path = run_log_path
        self.run_id = uuid.uuid4().hex[:8]
        self.verbose = verbose

    def _print(self, *args):
        if self.verbose:
            print(*args)

    def _cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        rates = self.PRICING.get(self.model)
        if rates is None:
            return 0.0
        return (
            prompt_tokens * rates["prompt"] + completion_tokens * rates["completion"]
        ) / 1_000_000

    def _log_usage(self, prompt: str, iteration: int, response, tool_names: List[str]):
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        cost = self._cost(prompt_tokens, completion_tokens)
        record = {
            "run_id": self.run_id,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "prompt": prompt[:200],
            "iteration": iteration,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
            "cost_usd": round(cost, 6),
            "tool_calls": tool_names,
            "history_len": len(self.chat_history),
        }
        for key in self.token_usage:
            self.token_usage[key] += record[key]
        self.cost_usd += cost

        Path(self.run_log_path).parent.mkdir(exist_ok=True)
        with open(self.run_log_path, "a") as f:
            f.write(json.dumps(record) + "\n")

        self._print(
            f"  [tokens] prompt={record['prompt_tokens']} "
            f"completion={record['completion_tokens']} "
            f"total={record['total_tokens']} "
            f"cost=${cost:.6f} (cumulative ${self.cost_usd:.6f})"
        )
        return record

    def run(self, user_prompt: str) -> str:
        self.chat_history.append({"role": "user", "content": user_prompt})
        start_cost = self.cost_usd

        try:
            for iteration in range(self.max_iterations):
                # Compact in place so the history actually shrinks, then record usage.
                before = len(self.chat_history)
                self.chat_history = self.context_manager.enforce_compaction(self.chat_history)
                if len(self.chat_history) < before:
                    self._print(f"  [compacted {before} -> {len(self.chat_history)} messages]")
                self.context_manager.track_burn(self.chat_history)

                self._print(f"Iteration {iteration}")
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self.chat_history,
                    tools=self.tools.get_tool_schemas(),
                )
                message = response.choices[0].message
                tool_names = [tc.function.name for tc in (message.tool_calls or [])]
                self._log_usage(user_prompt, iteration, response, tool_names)

                # No tool calls means the model has produced its final answer.
                if not message.tool_calls:
                    self.chat_history.append({"role": "assistant", "content": message.content})
                    return message.content

                # Record the assistant turn (with its tool call requests) before replying.
                self.chat_history.append(message)

                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    tool_arguments = tool_call.function.arguments
                    tool_result = self.tools.execute_tool(tool_name, tool_arguments)
                    self._print(f"Tool {tool_name} -> {str(tool_result)[:200]}")

                    # Feed the result back so the model can use it on the next iteration.
                    self.chat_history.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(tool_result),
                        }
                    )

            return "Max iterations reached"
        finally:
            self.last_run_cost = self.cost_usd - start_cost
            self._print(f"  [run cost] ${self.last_run_cost:.6f} (cumulative ${self.cost_usd:.6f})")


if __name__ == "__main__":  # quick CLI test: python -m agent.harness "your task"
    harness = AgentHarness()
    task = " ".join(sys.argv[1:]) or "What skills do you have available?"
    print(f">>> {task}\n")
    answer = harness.run(task)
    print(f"\nFinal answer: {answer}")
    print("-" * 40)
    print("Token usage this run:", harness.token_usage)
    print(f"Cost of this run: ${harness.last_run_cost:.6f}")
    print(f"Per-call run log written to: {harness.run_log_path}")
