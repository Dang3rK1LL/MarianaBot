"""Discord-independent access checks, controls and durable notification queue."""

import asyncio
import json
import re
import time
import uuid

from marianabot.config import Config
from marianabot.discord_config import DiscordConfig
from marianabot.store import Store
from marianabot.usage_ui import duration, quota_line
from marianabot.worker import WorkerManager


def excerpt(value: str, limit: int) -> str:
    value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value).strip()
    value = value.replace("@", "@\u200b")
    return (
        value
        if len(value) <= limit
        else value[: limit - 28].rstrip() + " … (full text in MarianaBot)"
    )


def section(plan: str, heading: str, limit: int) -> str:
    match = re.search(rf"(?im)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s|\Z)", plan, re.S)
    return excerpt(match[1], limit) if match else ""


def status_text(
    store: Store, run_id: str, *, now: float | None = None, include_models: bool = True
) -> str:
    run = store.run(run_id)
    config = Config.model_validate_json(run["config"])
    now = time.time() if now is None else now
    totals = store.usage_totals(run_id)
    compactions = store.db.execute(
        "SELECT COUNT(*),COUNT(summary) FROM compactions WHERE run_id=?", (run_id,)
    ).fetchone()
    memory = store.db.execute(
        "SELECT LENGTH(text) FROM research_memory WHERE run_id=?", (run_id,)
    ).fetchone()
    lines = [
        f"Run {run_id} · {run['status']} · round {run['round']}",
        f"Elapsed since creation: {duration(now - run['created'])} (includes waits/pauses)",
    ]
    if include_models:
        lines.append(
            f"RB/MB: {config.rb.model} / {config.rb.effort}; JB: {config.jb.model} / {config.jb.effort}"
        )
    if run["reason"]:
        lines.append("State: " + excerpt(run["reason"], 160))
    for provider, label in (("openai", "ChatGPT"), ("anthropic", "Claude")):
        row = totals.get(provider, {})
        counts = []
        for kind in ("input", "output"):
            value = row.get(f"{kind}_tokens", 0)
            counts.append(
                f"{value:,}" if row.get(f"{kind}_reports") or not row.get("calls") else "unknown"
            )
        partial = " · partial" if row.get("incomplete") else ""
        lines.append(f"{label}: {counts[0]} in / {counts[1]} out{partial}")
        lines.append(
            quota_line(store.get_limits(provider), demo=bool(run["demo"]), now=now).plain.strip()
        )
    lines += [
        f"App context budget: {config.research.max_context_chars:,} characters; native token window remaining: unknown",
        f"Saved summary: {memory[0] if memory else 0:,} characters; compactions: {compactions[1]} done / {compactions[0] - compactions[1]} pending",
    ]
    return excerpt("\n".join(lines), 1800)


def round_text(store: Store, run_id: str, row: dict) -> str:
    review = json.loads(row["review"]) if isinstance(row["review"], str) else row["review"]
    plan = row["plan"]
    summary = section(plan, "Round summary", 230)
    changes = section(plan, "Changes this round", 170) or "No separate change summary recorded."
    direction = excerpt(review.get("next_prompt", "Not recorded."), 230)
    heading = "Summary" if summary else "Plan excerpt"
    summary = summary or excerpt(plan, 230)
    blockers = "; ".join(review.get("blocking_issues", [])) or "None reported."
    return excerpt(
        f"Round {row['number']} complete · {review.get('score', '?')}/100 · {review.get('verdict', 'unknown')}\n"
        f"{heading}: {summary}\nChanges: {changes}\nNext: {direction}\n"
        f"Open issues: {excerpt(blockers, 130)}\n\n{status_text(store, run_id, include_models=False)}",
        1950,
    )


class DiscordBridge:
    def __init__(self, store: Store, config: DiscordConfig, manager=None):
        self.store, self.config = store, config
        self.manager = manager or WorkerManager(store.directory)
        self.guard = asyncio.Lock()
        self.scope = config.scope
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS discord_watch (
                scope TEXT PRIMARY KEY, run_id TEXT NOT NULL, epoch TEXT NOT NULL,
                last_round INTEGER NOT NULL, last_message INTEGER NOT NULL, last_state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS discord_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
                event_key TEXT NOT NULL, body TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0,
                UNIQUE(scope,event_key));
            CREATE TABLE IF NOT EXISTS discord_requests (
                scope TEXT NOT NULL, request_id TEXT NOT NULL, response TEXT NOT NULL,
                PRIMARY KEY(scope,request_id));
        """)

    def watch(self):
        row = self.store.db.execute(
            "SELECT * FROM discord_watch WHERE scope=?", (self.scope,)
        ).fetchone()
        return dict(row) if row else None

    def _response(self, request_id: str, response: str):
        with self.store.db:
            self.store.db.execute(
                "UPDATE discord_requests SET response=? WHERE scope=? AND request_id=?",
                (response, self.scope, request_id),
            )
        return response

    async def command(self, *, guild, channel, user, request_id, action, run_id="", message=""):
        self.config.authorize(guild, channel, user)
        if action not in {
            "sessions",
            "status",
            "watch",
            "unwatch",
            "ask",
            "steer",
            "pause",
            "resume",
            "retry",
        }:
            raise ValueError("Unknown command.")
        if action in {"ask", "steer", "pause", "resume", "retry"} and not self.config.allow_control:
            raise PermissionError(
                "Remote control is disabled in this installation's Discord settings."
            )
        async with self.guard:
            previous = self.store.db.execute(
                "SELECT response FROM discord_requests WHERE scope=? AND request_id=?",
                (self.scope, str(request_id)),
            ).fetchone()
            if previous:
                return previous[0]
            # Persist acceptance before side effects: an uncertain request is never replayed
            # automatically after a crash, even when Discord redelivers its interaction.
            with self.store.db:
                self.store.db.execute(
                    "INSERT INTO discord_requests VALUES(?,?,?)",
                    (
                        self.scope,
                        str(request_id),
                        "Earlier request recorded; outcome uncertain. Check /mariana status before issuing a new command.",
                    ),
                )
            try:
                response = await self._apply(action, run_id, message)
            except (ValueError, OSError) as exc:
                response = "Could not complete command: " + excerpt(str(exc), 500)
            return self._response(str(request_id), response)

    async def _apply(self, action, run_id, message):
        if action == "sessions":
            rows = self.store.db.execute(
                "SELECT id,status,round,SUBSTR(problem,1,90) AS problem FROM runs ORDER BY created DESC LIMIT 12"
            ).fetchall()
            return (
                "\n".join(
                    f"{r['id']} · {r['status']} · round {r['round']} · {excerpt(r['problem'], 90)}"
                    for r in rows
                )
                or "No saved research. Start a problem in MarianaBot first."
            )
        if action == "unwatch":
            with self.store.db:
                self.store.db.execute("DELETE FROM discord_watch WHERE scope=?", (self.scope,))
                self.store.db.execute(
                    "DELETE FROM discord_outbox WHERE scope=? AND delivered=0", (self.scope,)
                )
            return "Updates disconnected. Research continues on its host."
        if action == "watch":
            run = self.store.run(run_id)
            cursor = self.store.db.execute(
                "SELECT COALESCE(MAX(id),0) FROM chat_messages WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            with self.store.db:
                self.store.db.execute(
                    "DELETE FROM discord_outbox WHERE scope=? AND delivered=0", (self.scope,)
                )
                self.store.db.execute(
                    "INSERT OR REPLACE INTO discord_watch VALUES(?,?,?,?,?,?)",
                    (self.scope, run_id, uuid.uuid4().hex, run["round"], cursor, self._state(run)),
                )
            return (
                "Watching future rounds, MB replies and state changes in this channel.\n\n"
                + status_text(self.store, run_id)
            )
        watched = self.watch()
        if not watched:
            return "Select research with /mariana sessions, then /mariana watch run_id."
        run_id = watched["run_id"]
        run = self.store.run(run_id)
        if action == "status":
            return status_text(self.store, run_id)
        if action in ("ask", "steer"):
            command_id = self.store.enqueue(run_id, action, message)
            if action == "ask":
                try:
                    await self.manager.start(run_id, messages_only=True)
                except (ValueError, OSError):
                    return f"Question #{command_id} is saved, but MB could not start. Check the local worker and use /mariana retry."
            return f"Saved {action} #{command_id}. " + (
                "MB's reply will appear here; allowance waits still apply."
                if action == "ask"
                else "Applied at the next research round boundary. A paused run stays paused until /mariana resume."
            )
        if action == "retry":
            if not self.store.pending_questions(run_id):
                return "No unanswered MB questions."
            await self.manager.start(run_id, messages_only=True)
            return "MB reply worker requested. Research is not resumed."
        if run["status"] in ("complete", "stopped"):
            return "This research is closed. You can still ask MB questions or start a new problem locally."
        if action == "pause":
            self.store.update_run(
                run_id, control="pause", status="paused", reason="Paused through Discord"
            )
            self.store.event(run_id, "Owner requested pause through Discord")
            return "Pause requested. Completed work is saved; an in-flight request may already have consumed usage."
        await self.manager.start(run_id)
        return "Research worker requested. Saved model choices, billing controls and quota waits still apply."

    @staticmethod
    def _state(run):
        return json.dumps([run["status"], run["reason"]])

    def _queue(self, key: str, body: str):
        self.store.db.execute(
            "INSERT OR IGNORE INTO discord_outbox(scope,event_key,body) VALUES(?,?,?)",
            (self.scope, key, body),
        )

    def collect(self):
        """Commit messages and their source cursors together, before any network call."""
        watched = self.watch()
        if not watched:
            return
        run_id, epoch = watched["run_id"], watched["epoch"]
        run = self.store.run(run_id)
        with self.store.transaction():
            for row in self.store.db.execute(
                "SELECT * FROM rounds WHERE run_id=? AND number>? ORDER BY number LIMIT 10",
                (run_id, watched["last_round"]),
            ).fetchall():
                self._queue(
                    f"{epoch}:round:{row['number']}", round_text(self.store, run_id, dict(row))
                )
                watched["last_round"] = row["number"]
            for row in self.store.db.execute(
                "SELECT id,title,text FROM chat_messages WHERE run_id=? AND id>? AND role='MB' ORDER BY id LIMIT 20",
                (run_id, watched["last_message"]),
            ).fetchall():
                self._queue(
                    f"{epoch}:message:{row['id']}",
                    f"MB · {excerpt(row['title'], 80)} · {run_id}\n{excerpt(row['text'], 1750)}",
                )
                watched["last_message"] = row["id"]
            state = self._state(run)
            if state != watched["last_state"]:
                self._queue(f"{epoch}:state:{uuid.uuid4().hex}", status_text(self.store, run_id))
            self.store.db.execute(
                "UPDATE discord_watch SET last_round=?,last_message=?,last_state=? WHERE scope=?",
                (watched["last_round"], watched["last_message"], state, self.scope),
            )

    async def deliver(self, send):
        async with self.guard:
            self.collect()
            rows = self.store.db.execute(
                "SELECT id,body FROM discord_outbox WHERE scope=? AND delivered=0 ORDER BY id LIMIT 5",
                (self.scope,),
            ).fetchall()
            for row in rows:
                await send(row["body"])
                with self.store.db:
                    self.store.db.execute(
                        "UPDATE discord_outbox SET delivered=1 WHERE id=?", (row["id"],)
                    )
