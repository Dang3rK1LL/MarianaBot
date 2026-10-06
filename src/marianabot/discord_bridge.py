"""Discord-independent access checks, controls and durable notification queue."""

import asyncio
import json
import re
import time
import uuid

from marianabot.config import Config
from marianabot.discord_config import DiscordConfig
from marianabot.discord_messages import (
    card_text,
    message_card,
    recap_card,
    round_start_card,
    stage_card,
    state_card,
    wait_card,
)
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


def status_text(store: Store, run_id: str, *, now: float | None = None) -> str:
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
        f"RB/MB: {config.rb.model} / {config.rb.effort}; JB: {config.jb.model} / {config.jb.effort}",
    ]
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


def usage_snapshot(store: Store, run_id: str) -> dict:
    return dict(
        usage=store.usage_totals(run_id), elapsed=time.time() - store.run(run_id)["created"]
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
        if "last_event" not in {
            row[1] for row in store.db.execute("PRAGMA table_info(discord_watch)")
        }:
            with store.db:
                store.db.execute(
                    "ALTER TABLE discord_watch ADD COLUMN last_event INTEGER NOT NULL DEFAULT 0"
                )
        if "embed" not in {row[1] for row in store.db.execute("PRAGMA table_info(discord_outbox)")}:
            with store.db:
                store.db.execute("ALTER TABLE discord_outbox ADD COLUMN embed TEXT")

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
            event_cursor = self.store.db.execute(
                "SELECT COALESCE(MAX(id),0) FROM events WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            with self.store.db:
                self.store.db.execute(
                    "DELETE FROM discord_outbox WHERE scope=? AND delivered=0", (self.scope,)
                )
                self.store.db.execute(
                    "INSERT OR REPLACE INTO discord_watch(scope,run_id,epoch,last_round,last_message,last_state,last_event) VALUES(?,?,?,?,?,?,?)",
                    (
                        self.scope,
                        run_id,
                        uuid.uuid4().hex,
                        run["round"],
                        cursor,
                        self._state(run),
                        event_cursor,
                    ),
                )
            return (
                "Watching research stages, recaps, MB replies and state changes in this channel.\n\n"
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

    def _queue(self, key: str, embed: dict):
        self.store.db.execute(
            "INSERT OR IGNORE INTO discord_outbox(scope,event_key,body,embed) VALUES(?,?,?,?)",
            (self.scope, key, card_text(embed), json.dumps(embed)),
        )

    def _recap(self, run: dict, row: dict, snapshot: dict | None = None) -> dict:
        previous = self.store.db.execute(
            "SELECT review FROM rounds WHERE run_id=? AND number<? ORDER BY number DESC LIMIT 1",
            (run["id"], row["number"]),
        ).fetchone()
        return recap_card(
            run,
            row,
            snapshot if snapshot is not None else usage_snapshot(self.store, run["id"]),
            json.loads(previous["review"]) if previous else None,
        )

    def collect(self):
        """Commit messages and their source cursors together, before any network call."""
        watched = self.watch()
        if not watched:
            return
        run_id, epoch = watched["run_id"], watched["epoch"]
        run = self.store.run(run_id)
        with self.store.transaction():
            events = self.store.db.execute(
                "SELECT id,kind,payload,created FROM events WHERE run_id=? AND id>? "
                "AND kind IN ('round_started','round_complete','research_stage','research_wait') ORDER BY id LIMIT 100",
                (run_id, watched["last_event"]),
            ).fetchall()
            messages = self.store.db.execute(
                "SELECT * FROM chat_messages WHERE run_id=? AND id>? AND role='MB' ORDER BY id LIMIT 100",
                (run_id, watched["last_message"]),
            ).fetchall()
            sources = sorted(
                [(row["created"], "event", row["id"], dict(row)) for row in events]
                + [(row["created"], "message", row["id"], dict(row)) for row in messages]
            )[:100]
            for _, source, _, event in sources:
                if source == "message":
                    self._queue(f"{epoch}:message:{event['id']}", message_card(run, event))
                    watched["last_message"] = event["id"]
                    continue
                payload = json.loads(event["payload"])
                if event["kind"] == "round_started":
                    self._queue(
                        f"{epoch}:start:{payload['number']}:{payload['revision']}",
                        round_start_card(run, payload),
                    )
                elif event["kind"] == "research_stage":
                    if payload.get("stage") in {"intake", "synthesis", "critique", "verdict"}:
                        self._queue(
                            f"{epoch}:stage:{payload['number']}:{payload['revision']}:{payload['stage']}",
                            stage_card(run, payload),
                        )
                elif event["kind"] == "research_wait":
                    self._queue(f"{epoch}:wait:{event['id']}", wait_card(run, payload))
                else:
                    row = self.store.db.execute(
                        "SELECT * FROM rounds WHERE run_id=? AND number=?",
                        (run_id, payload["number"]),
                    ).fetchone()
                    if row:
                        self._queue(
                            f"{epoch}:round:{row['number']}",
                            self._recap(run, dict(row), payload),
                        )
                        watched["last_round"] = max(watched["last_round"], row["number"])
                watched["last_event"] = event["id"]
            if len(sources) == 100:
                self.store.db.execute(
                    "UPDATE discord_watch SET last_event=?,last_round=?,last_message=? WHERE scope=?",
                    (
                        watched["last_event"],
                        watched["last_round"],
                        watched["last_message"],
                        self.scope,
                    ),
                )
                return
            for row in self.store.db.execute(
                "SELECT * FROM rounds WHERE run_id=? AND number>? ORDER BY number LIMIT 10",
                (run_id, watched["last_round"]),
            ).fetchall():
                self._queue(f"{epoch}:round:{row['number']}", self._recap(run, dict(row)))
                watched["last_round"] = row["number"]
            state = self._state(run)
            if state != watched["last_state"]:
                history = self.store.rounds(run_id)
                self._queue(
                    f"{epoch}:state:{uuid.uuid4().hex}",
                    state_card(
                        run,
                        usage_snapshot(self.store, run_id),
                        history[-1]["review"] if history else None,
                    ),
                )
            self.store.db.execute(
                "UPDATE discord_watch SET last_round=?,last_message=?,last_state=?,last_event=? WHERE scope=?",
                (
                    watched["last_round"],
                    watched["last_message"],
                    state,
                    watched["last_event"],
                    self.scope,
                ),
            )

    async def deliver(self, send):
        async with self.guard:
            self.collect()
            rows = self.store.db.execute(
                "SELECT id,body,embed FROM discord_outbox WHERE scope=? AND delivered=0 ORDER BY id LIMIT 5",
                (self.scope,),
            ).fetchall()
            for row in rows:
                if row["embed"]:
                    await send(row["body"], embed=json.loads(row["embed"]))
                else:
                    await send(row["body"])
                with self.store.db:
                    self.store.db.execute(
                        "UPDATE discord_outbox SET delivered=1 WHERE id=?", (row["id"],)
                    )
