try:
    import tiktoken
except ImportError:
    tiktoken = None


class ContextManager:
    def __init__(
        self,
        max_context_tokens: int = 500,
        client=None,
        model: str = "gpt-4o",
        keep_recent: int = 4,
    ):
        self.context = []
        self.max_context_tokens = max_context_tokens
        self.metrics = {"input_tokens": 0, "output_tokens": 0}
        # Optional OpenAI client. If provided, compaction summarizes old turns
        # with the model; otherwise it falls back to truncation.
        self.client = client
        self.model = model
        self.keep_recent = keep_recent
        self._encoder = None
        if tiktoken is not None:
            try:
                self._encoder = tiktoken.encoding_for_model(model)
            except KeyError:
                self._encoder = tiktoken.get_encoding("cl100k_base")

    def track_burn(self, context):
        self.metrics["input_tokens"] += self.estimate_tokens(context)

    def enforce_compaction(self, context):
        if self.estimate_tokens(context) <= self.max_context_tokens:
            return context
        return self.compact(context)

    def compact(self, context):
        # System messages are always preserved verbatim.
        system_msgs = [m for m in context if self._role(m) == "system"]
        convo = [m for m in context if self._role(m) != "system"]

        split = self._safe_split_index(convo)
        older, recent = convo[:split], convo[split:]

        if not older:
            # Nothing safe to compact (e.g. all messages are recent/paired).
            return context

        summary = self._summarize(older)
        return system_msgs + [summary] + recent

    def _safe_split_index(self, convo):
        """Index that keeps the last `keep_recent` messages, never splitting an
        assistant tool-call from the `tool` results that must follow it."""
        if len(convo) <= self.keep_recent:
            return 0
        split = len(convo) - self.keep_recent
        # A `tool` message must be preceded by its assistant tool_calls turn,
        # so walk the boundary back until `recent` doesn't start with one.
        while split < len(convo) and self._role(convo[split]) == "tool":
            split += 1
        return split

    def _summarize(self, messages):
        transcript = "\n".join(f"{self._role(m)}: {self._content(m)}" for m in messages)

        if self.client is None:
            # No model available: fall back to a truncated transcript.
            return {
                "role": "system",
                "content": f"[Earlier conversation, truncated]\n{transcript[:1000]}",
            }

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "Summarize the following conversation, preserving facts, "
                    "decisions, and tool results needed to continue. Be concise.",
                },
                {"role": "user", "content": transcript},
            ],
        )
        summary_text = response.choices[0].message.content
        return {"role": "system", "content": f"[Summary of earlier conversation]\n{summary_text}"}

    def estimate_tokens(self, context):
        messages = context if isinstance(context, list) else [context]
        text = "\n".join(f"{self._role(m)} {self._content(m)}" for m in messages)
        if self._encoder is not None:
            return len(self._encoder.encode(text))
        # ~4 characters per token is a decent rough heuristic.
        return len(text) // 4 + 1

    def _role(self, message):
        if isinstance(message, dict):
            return message.get("role", "")
        return getattr(message, "role", "")

    def _content(self, message):
        if isinstance(message, dict):
            parts = [str(message.get("content") or "")]
            for call in message.get("tool_calls") or []:
                fn = call.get("function", {})
                parts.append(f"{fn.get('name', '')}({fn.get('arguments', '')})")
            return " ".join(p for p in parts if p)

        parts = [str(getattr(message, "content", "") or "")]
        for call in getattr(message, "tool_calls", None) or []:
            parts.append(f"{call.function.name}({call.function.arguments})")
        return " ".join(p for p in parts if p)
