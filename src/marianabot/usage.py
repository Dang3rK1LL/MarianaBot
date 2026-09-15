"""Provider-reported token counts, without estimates or double-counted stream events."""

FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def token_count(value):
    return value if type(value) is int and 0 <= value < 2**53 else None


def normalize_usage(provider: str, usage) -> dict | None:
    if not isinstance(usage, dict):
        return None
    fresh = token_count(usage.get("input_tokens"))
    output = token_count(usage.get("output_tokens"))
    read = token_count(
        usage.get("cached_input_tokens" if provider == "openai" else "cache_read_input_tokens")
    )
    write = (
        token_count(usage.get("cache_creation_input_tokens")) if provider == "anthropic" else None
    )
    if all(value is None for value in (fresh, output, read, write)):
        return None
    # Codex input already includes cached tokens; Claude reports three disjoint input buckets.
    total_input = fresh
    if provider == "anthropic" and any(v is not None for v in (fresh, read, write)):
        total_input = (fresh or 0) + (read or 0) + (write or 0)
    return dict(
        input_tokens=total_input,
        output_tokens=output,
        cache_read_tokens=read or 0,
        cache_write_tokens=write or 0,
    )


class UsageStream:
    """One native invocation. Final totals replace, rather than add to, partial totals."""

    def __init__(self, provider: str):
        self.provider = provider
        self.messages = {}
        self.current = None
        self.latest = None
        self.final = False

    def observe(self, event: dict) -> tuple[dict, bool] | None:
        before = self.latest, self.final
        kind = event.get("type")
        if self.provider == "openai":
            if kind == "turn.completed":
                self.latest = normalize_usage(self.provider, event.get("usage"))
                self.final = self.latest is not None
        elif kind == "result":
            raw = event.get("usage")
            # Single-shot modelUsage covers the whole invocation, including helper requests.
            models = event.get("modelUsage")
            if isinstance(models, dict):
                totals = {}
                for value in models.values():
                    if not isinstance(value, dict):
                        continue
                    for source, target in (
                        ("inputTokens", "input_tokens"),
                        ("outputTokens", "output_tokens"),
                        ("cacheReadInputTokens", "cache_read_input_tokens"),
                        ("cacheCreationInputTokens", "cache_creation_input_tokens"),
                    ):
                        count = token_count(value.get(source))
                        if count is not None:
                            totals[target] = totals.get(target, 0) + count
                if totals:
                    raw = totals
            final = normalize_usage(self.provider, raw)
            if final is not None:
                self.final = True
                if event.get("is_error") or event.get("subtype") != "success":
                    # A crashed Claude process can emit zeroed result usage. Retain observed work.
                    for key, value in (self.latest or {}).items():
                        if value is not None and (final.get(key) is None or value > final[key]):
                            final[key] = value
                            self.final = False
                self.latest = final
        elif not self.final and not event.get("parent_tool_use_id"):
            message, delta, message_id = {}, False, None
            if kind == "stream_event":
                inner = event.get("event", {})
                if inner.get("type") == "message_start":
                    message = inner.get("message", {})
                    self.current = message_id = message.get("id")
                elif inner.get("type") == "message_delta":
                    message_id, message, delta = self.current, inner, True
                elif inner.get("type") == "message_stop":
                    self.current = None
            elif kind == "assistant":
                message = event.get("message", {})
                message_id = message.get("id")
            raw = message.get("usage")
            if message_id and isinstance(raw, dict):
                previous = self.messages.setdefault(message_id, {})
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                ):
                    # Assistant/message_start output counts are placeholders. Deltas are cumulative.
                    if key == "output_tokens" and not delta:
                        continue
                    value = token_count(raw.get(key))
                    if value is not None:
                        previous[key] = max(previous.get(key, 0), value)
                totals = {}
                for values in self.messages.values():
                    for key, value in values.items():
                        totals[key] = totals.get(key, 0) + value
                self.latest = normalize_usage(self.provider, totals)
        if self.latest is not None and before != (self.latest, self.final):
            return self.latest, self.final
        return None
