"""Working state from live chair output; exact context excerpts retain archived sources."""

import hashlib
import json
import re
import time

from marianabot.clients import ClientError

PROTECTED = {
    "problem",
    "owner_problem",
    "owner_message",
    "owner_feedback",
    "protected_notes",
    "research_contract",
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


def size(value):
    return len(encoded(value))


class Compactor:
    def __init__(self, engine):
        self.engine = engine
        self.store, self.run_id = engine.store, engine.run_id
        self.limit = engine.config.research.max_context_chars

    def pack(self, text: str, target: int, label: str) -> str:
        if len(encoded(text)) <= target:
            return text
        key = hashlib.sha256(
            encoded(["context-v3", self.run_id, text, target]).encode()
        ).hexdigest()
        saved = self.store.db.execute(
            "SELECT summary FROM compactions WHERE id=?", (key,)
        ).fetchone()
        if saved and saved[0]:
            return saved[0]
        # This is a labeled selection of real source passages for input assembly,
        # never a generated answer or a replacement for a model response.
        result = extractive_summary(text, max(96, target // 3))
        if len(encoded(result)) > target:
            raise ClientError("Research context cannot fit safely; full sources remain archived")
        with self.store.db:
            self.store.db.execute(
                "INSERT OR REPLACE INTO compactions VALUES(?,?,?,?,?,?)",
                (key, self.run_id, label, text, result, time.time()),
            )
        return result

    async def prepare(self, parts: dict, limit: int) -> dict:
        if size(parts) <= limit:
            return parts
        protected = {key: value for key, value in parts.items() if key in PROTECTED}
        remaining = limit - size(protected) - 512
        flexible = sorted(
            [
                (key, value if isinstance(value, str) else encoded(value))
                for key, value in parts.items()
                if key not in PROTECTED
            ],
            key=lambda item: len(encoded(item[1])),
        )
        if remaining < max(512, len(flexible) * 160):
            raise ClientError(
                "Owner instructions exceed the context limit; nothing was discarded. Increase research.max_context_chars before resuming."
            )
        result = dict(protected)
        for index, (key, text) in enumerate(flexible):
            share = max(128, remaining // (len(flexible) - index) - len(key) - 16)
            packed = self.pack(text, share, key)
            result[key] = packed
            remaining -= len(encoded(packed)) + len(key) + 8
        if size(result) > limit:
            raise ClientError(
                "Prepared context exceeds the configured limit; full owner instructions are preserved"
            )
        return result

    async def advance(self):
        history = self.store.rounds(self.run_id)
        if not history:
            return
        row = history[-1]
        memory = self.store.memory(self.run_id)
        if memory["through_round"] >= row["number"]:
            return
        review = row["review"]
        text = review.get("carry_forward") or encoded(
            {
                "round": row["number"],
                "finding": review.get("summary", ""),
                "plan": row["plan"],
                "dissent": review.get("dissent", []),
                "limitations": review.get("limitations", []),
                "open": review.get("blocking_issues", []),
                "deliverables": review.get("deliverables", {}),
            }
        )
        text = self.pack(
            text, self.engine.config.research.memory_chars, "live review working state"
        )
        self.store.save_memory(
            self.run_id,
            row["number"],
            text,
            [c["id"] for c in self.store.commands(self.run_id) if c["answer"] is not None],
        )

    def context(self):
        run = self.store.run(self.run_id)
        history = self.store.rounds(self.run_id)
        return {
            "research_memory": self.store.memory(self.run_id)["text"],
            "research_contract": json.loads(run.get("contract") or "{}"),
            "protected_notes": [
                dict(id=p["id"], text=p["text"], source=p["source"])
                for p in self.store.pins(self.run_id)
                if p["kind"] == "owner"
            ],
            "owner_feedback": self.store.owner_feedback(self.run_id),
            "current_issues": {
                key: history[-1]["review"].get(key, [])
                for key in (
                    "blocking_issues",
                    "dissent",
                    "online_checks",
                    "limitations",
                    "deliverables",
                )
            }
            if history
            else {},
        }


def extractive_summary(text: str, target: int, *, max_words: int | None = None) -> str:
    """Copy bounded original passages; disclose omissions instead of inventing a digest."""
    header = "Partial archive extract; omissions remain in the full saved source.\n"
    remaining = max(0, target - len(header))
    remaining_words = None if max_words is None else max(0, max_words - len(header.split()))
    passages = re.split(r"\n+|(?<=[.!?])\s+(?=[A-Z0-9])", text)
    indexed = [(i, passage.strip()) for i, passage in enumerate(passages) if passage.strip()]
    priority = re.compile(
        r"https?://|\d|evidence|constraint|decision|unknown|unverified|contradict|reject|dissent|limitation",
        re.I,
    )
    selected = []
    for index, passage in sorted(
        indexed, key=lambda item: (not bool(priority.search(item[1])), item[0])
    ):
        count = len(passage.split())
        if len(passage) + 1 <= remaining and (remaining_words is None or count <= remaining_words):
            selected.append((index, passage))
            remaining -= len(passage) + 1
            if remaining_words is not None:
                remaining_words -= count
    if not selected and indexed:
        # A single enormous line cannot be preserved in a bounded summary. Keep
        # complete words and avoid a cut through a URL; the archive keeps all bytes.
        passage = indexed[0][1][:remaining].rsplit(" ", 1)[0]
        if remaining_words is not None:
            words = list(re.finditer(r"\S+", passage))
            if len(words) > remaining_words:
                passage = passage[: words[remaining_words - 1].end()] if remaining_words else ""
        passage = re.sub(r"https?://\S*$", "", passage)
        selected.append((0, passage))
    return (header + "\n".join(passage for _, passage in sorted(selected)))[:target]
