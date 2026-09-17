"""Durable checkpoints, user mailbox, and call history. No credentials are stored."""

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from marianabot.config import Config
from marianabot.usage import FIELDS, normalize_usage


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory.resolve()
        self.db = sqlite3.connect(directory / "mariana.sqlite3", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, problem TEXT NOT NULL, brief TEXT NOT NULL DEFAULT '',
                config TEXT NOT NULL, demo INTEGER NOT NULL, created REAL NOT NULL,
                status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                control TEXT NOT NULL DEFAULT '', round INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS calls (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, task TEXT NOT NULL,
                brain TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, result TEXT);
            CREATE INDEX IF NOT EXISTS calls_task ON calls(run_id, task, state);
            CREATE TABLE IF NOT EXISTS rounds (
                run_id TEXT NOT NULL, number INTEGER NOT NULL, revision INTEGER NOT NULL,
                plan TEXT NOT NULL, review TEXT NOT NULL, PRIMARY KEY(run_id, number));
            CREATE TABLE IF NOT EXISTS commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                kind TEXT NOT NULL, text TEXT NOT NULL, answer TEXT, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS limits (
                provider TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                created REAL NOT NULL, text TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                message_key TEXT NOT NULL, role TEXT NOT NULL, title TEXT NOT NULL,
                text TEXT NOT NULL, created REAL NOT NULL, UNIQUE(run_id, message_key));
            CREATE TABLE IF NOT EXISTS call_usage (
                call_id TEXT PRIMARY KEY, input_tokens INTEGER, output_tokens INTEGER,
                cache_read_tokens INTEGER NOT NULL, cache_write_tokens INTEGER NOT NULL,
                reported_at REAL NOT NULL, final INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS calls_run ON calls(run_id, provider);
            CREATE TABLE IF NOT EXISTS compactions (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, label TEXT NOT NULL,
                source TEXT NOT NULL, summary TEXT, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS research_memory (
                run_id TEXT PRIMARY KEY, through_round INTEGER NOT NULL, text TEXT NOT NULL,
                commands TEXT NOT NULL DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS memory_pins (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL,
                text TEXT NOT NULL, source TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
        """)
        # Additive migration for stores created before the interactive chat.
        with self.transaction():
            if "commands" not in {
                row[1] for row in self.db.execute("PRAGMA table_info(research_memory)")
            }:
                self.db.execute(
                    "ALTER TABLE research_memory ADD COLUMN commands TEXT NOT NULL DEFAULT '[]'"
                )
            if "control_epoch" not in {
                row[1] for row in self.db.execute("PRAGMA table_info(runs)")
            }:
                self.db.execute(
                    "ALTER TABLE runs ADD COLUMN control_epoch INTEGER NOT NULL DEFAULT 0"
                )
            # Backfill older responses once; live summaries never scan large prompt/result blobs.
            for row in self.db.execute(
                "SELECT id,provider,result,created FROM calls WHERE result IS NOT NULL AND id NOT IN (SELECT call_id FROM call_usage)"
            ).fetchall():
                result = json.loads(row["result"])
                usage = normalize_usage(row["provider"], result.get("usage"))
                if usage is not None:
                    self._record_usage(row["id"], usage, True, row["created"])

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def create_run(self, problem: str, config: Config, demo: bool = False) -> str:
        if not problem.strip() or len(problem) > 100000:
            raise ValueError("The problem must contain 1–100,000 characters")
        run_id = uuid.uuid4().hex[:12]
        with self.db:
            self.db.execute(
                "INSERT INTO runs(id,problem,config,demo,created,status) VALUES(?,?,?,?,?,?)",
                (run_id, problem, config.model_dump_json(), demo, time.time(), "ready"),
            )
            self._message(run_id, "problem", "you", "Your problem", problem)
        return run_id

    def message(self, run_id: str, key: str, role: str, title: str, text: str):
        with self.db:
            self._message(run_id, key, role, title, text)

    def _message(self, run_id: str, key: str, role: str, title: str, text: str):
        self.db.execute(
            "INSERT OR IGNORE INTO chat_messages(run_id,message_key,role,title,text,created) VALUES(?,?,?,?,?,?)",
            (run_id, key, role, title, text, time.time()),
        )

    def messages(self, run_id: str, after: int = 0) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM chat_messages WHERE run_id=? AND id>? ORDER BY id", (run_id, after)
            )
        ]

    def seed_chat(self, run_id: str):
        """Import existing checkpoints once; safe to reopen legacy CLI sessions."""
        if self.messages(run_id):
            return
        run = self.run(run_id)
        self.message(run_id, "problem", "you", "Your problem", run["problem"])
        if run["brief"]:
            self.message(run_id, "intake", "MB", "Research brief", run["brief"])
        for row in self.rounds(run_id):
            self.publish_round(run_id, row["number"], row["revision"], row["plan"], row["review"])
        for command in self.commands(run_id):
            self.message(
                run_id, f"command-{command['id']}", "you", command["kind"], command["text"]
            )
            if command["answer"]:
                self.message(run_id, f"answer-{command['id']}", "MB", "Reply", command["answer"])

    def activity(self, run_id: str) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT brain,state,COUNT(*) AS count,SUM(task LIKE 'memory-%') AS compacting FROM calls WHERE run_id=? GROUP BY brain,state",
                (run_id,),
            )
        ]

    def publish_round(self, run_id: str, number: int, revision: int, plan: str, review: dict):
        prefix = f"round-{number}-revision-{revision}"
        self.message(run_id, prefix + "-plan", "RB", f"Round {number} · Research plan", plan)
        sections = [f"**{review['score']}/100 · {review['verdict'].replace('_', ' ')}**"]
        for key, label in (
            ("strengths", "Strengths"),
            ("blocking_issues", "Blocking issues"),
            ("human_tests", "Tests for you"),
            ("dissent", "Dissent"),
        ):
            if review.get(key):
                sections.append("### " + label + "\n" + "\n".join(f"- {v}" for v in review[key]))
        sections.append("### Next research prompt\n" + review["next_prompt"])
        self.message(
            run_id,
            prefix + "-review",
            "JB",
            f"Round {number} · Critical review",
            "\n\n".join(sections),
        )

    def run(self, run_id: str) -> dict:
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError(f"Unknown run: {run_id}")
        return dict(row)

    def runs(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM runs ORDER BY created DESC")]

    def update_run(self, run_id: str, **changes):
        allowed = {"status", "reason", "control", "brief", "round", "revision"}
        if not changes or not set(changes) <= allowed:
            raise ValueError("Invalid run update")
        with self.db:
            self.db.execute(
                f"UPDATE runs SET {','.join(k + '=?' for k in changes)} WHERE id=?",
                (*changes.values(), run_id),
            )
            if "control" in changes:
                self.db.execute(
                    "UPDATE runs SET control_epoch=control_epoch+1 WHERE id=?", (run_id,)
                )

    def event(self, run_id: str, message: str):
        with self.db:
            self.db.execute(
                "INSERT INTO events(run_id,created,text) VALUES(?,?,?)",
                (run_id, time.time(), message),
            )

    def events(self, run_id: str, count: int = 12) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM events WHERE run_id=? ORDER BY id DESC LIMIT ?", (run_id, count)
            )
        ]

    def cached(self, run_id: str, task: str):
        row = self.db.execute(
            "SELECT result FROM calls WHERE run_id=? AND task=? AND state='done' ORDER BY created DESC LIMIT 1",
            (run_id, task),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def recover(self):
        # Caller must hold the single-worker file lock.
        with self.db:
            self.db.execute("UPDATE calls SET state='unknown' WHERE state='running'")

    def begin_call(self, run_id: str, task: str, brain: str, provider: str, model: str) -> str:
        call_id = uuid.uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)",
                (call_id, run_id, task, brain, provider, model, "running", time.time(), None),
            )
        return call_id

    def finish(self, call_id: str, state: str, result=None):
        with self.db:
            if result:
                provider = self.db.execute(
                    "SELECT provider FROM calls WHERE id=?", (call_id,)
                ).fetchone()[0]
                usage = result.get("token_usage") or normalize_usage(provider, result.get("usage"))
                if usage is not None:
                    self._record_usage(call_id, usage, True, time.time())
            self.db.execute(
                "UPDATE calls SET state=?,result=? WHERE id=?",
                (state, json.dumps(result) if result is not None else None, call_id),
            )

    def _record_usage(self, call_id: str, usage: dict, final: bool, reported_at: float):
        self.db.execute(
            "INSERT INTO call_usage VALUES(?,?,?,?,?,?,?) ON CONFLICT(call_id) DO UPDATE SET input_tokens=excluded.input_tokens,output_tokens=excluded.output_tokens,cache_read_tokens=excluded.cache_read_tokens,cache_write_tokens=excluded.cache_write_tokens,reported_at=excluded.reported_at,final=excluded.final WHERE call_usage.final=0 OR excluded.final=1",
            (call_id, *(usage[key] for key in FIELDS), reported_at, int(final)),
        )

    def record_usage(self, call_id: str, usage: dict, final: bool = False):
        with self.db:
            self._record_usage(call_id, usage, final, time.time())

    def usage_totals(self, run_id: str) -> dict[str, dict]:
        rows = self.db.execute(
            """
            SELECT c.provider, COUNT(*) AS calls, COUNT(u.call_id) AS reported_calls,
                   COUNT(u.input_tokens) AS input_reports, COUNT(u.output_tokens) AS output_reports,
                   COALESCE(SUM(u.input_tokens),0) AS input_tokens,
                   COALESCE(SUM(u.output_tokens),0) AS output_tokens,
                   COALESCE(SUM(u.cache_read_tokens),0) AS cache_read_tokens,
                   COALESCE(SUM(u.cache_write_tokens),0) AS cache_write_tokens,
                   SUM(c.state='running') AS active,
                   SUM(CASE WHEN u.final=1 AND u.input_tokens IS NOT NULL AND u.output_tokens IS NOT NULL THEN 0 ELSE 1 END) AS incomplete,
                   MAX(u.reported_at) AS reported_at
            FROM calls c LEFT JOIN call_usage u ON c.id=u.call_id
            WHERE c.run_id=? GROUP BY c.provider
        """,
            (run_id,),
        )
        return {row["provider"]: dict(row) for row in rows}

    def calls(self, run_id: str) -> list[dict]:
        return [
            dict(r) | {"result": json.loads(r["result"]) if r["result"] else None}
            for r in self.db.execute(
                "SELECT * FROM calls WHERE run_id=? ORDER BY created", (run_id,)
            )
        ]

    def save_round(self, run_id: str, number: int, revision: int, plan: str, review: dict):
        with self.transaction():
            self.db.execute(
                "INSERT OR REPLACE INTO rounds VALUES(?,?,?,?,?)",
                (run_id, number, revision, plan, json.dumps(review)),
            )
            self.db.execute("UPDATE runs SET round=? WHERE id=?", (number, run_id))
            self._protect_review(run_id, number, review)

    def _protect_review(self, run_id: str, number: int, review: dict):
        for kind in ("blocking_issues", "dissent", "human_tests"):
            for text in review.get(kind, []):
                self._pin(run_id, kind, text, f"round {number}")

    def protect_reviews(self, run_id: str):
        # Also cover research created by versions without compaction.
        with self.db:
            for row in self.rounds(run_id):
                self._protect_review(run_id, row["number"], row["review"])

    def _pin(self, run_id: str, kind: str, text: str, source: str) -> str:
        key = hashlib.sha256((run_id + kind + text).encode()).hexdigest()[:16]
        self.db.execute(
            "INSERT OR IGNORE INTO memory_pins(id,run_id,kind,text,source) VALUES(?,?,?,?,?)",
            (key, run_id, kind, text, source),
        )
        return key

    def pin(self, run_id: str, text: str) -> str:
        self.run(run_id)
        if not text.strip() or len(text) > 10000:
            raise ValueError("Use /pin followed by 1–10,000 characters to preserve verbatim.")
        with self.db:
            key = self._pin(run_id, "owner", text, "owner pin")
            self.db.execute("UPDATE memory_pins SET active=1 WHERE id=?", (key,))
        return key

    def unpin(self, run_id: str, key: str):
        with self.db:
            result = self.db.execute(
                "UPDATE memory_pins SET active=0 WHERE run_id=? AND id=? AND active=1",
                (run_id, key),
            )
            if not result.rowcount:
                raise ValueError("No active pin with that ID. Use /memory to see the IDs.")

    def pins(self, run_id: str, *, active_only=True) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM memory_pins WHERE run_id=?"
                + (" AND active=1" if active_only else "")
                + " ORDER BY rowid",
                (run_id,),
            )
        ]

    def memory(self, run_id: str) -> dict:
        row = self.db.execute("SELECT * FROM research_memory WHERE run_id=?", (run_id,)).fetchone()
        return (
            (dict(row) | {"commands": json.loads(row["commands"])})
            if row
            else {"run_id": run_id, "through_round": 0, "text": "", "commands": []}
        )

    def save_memory(self, run_id: str, through_round: int, text: str, commands=None):
        commands = self.memory(run_id)["commands"] if commands is None else commands
        with self.db:
            self.db.execute(
                "INSERT INTO research_memory VALUES(?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET through_round=excluded.through_round,text=excluded.text,commands=excluded.commands",
                (run_id, through_round, text, json.dumps(commands)),
            )

    def compactions(self, run_id: str) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM compactions WHERE run_id=? ORDER BY created", (run_id,)
            )
        ]

    def rounds(self, run_id: str) -> list[dict]:
        return [
            dict(r) | {"review": json.loads(r["review"])}
            for r in self.db.execute(
                "SELECT * FROM rounds WHERE run_id=? ORDER BY number", (run_id,)
            )
        ]

    def enqueue(self, run_id: str, kind: str, message: str) -> int:
        run = self.run(run_id)
        if kind == "steer" and run["status"] in ("complete", "stopped"):
            raise ValueError("This run is closed; create a new run using the exported plan")
        if kind not in ("ask", "steer") or not message.strip() or len(message) > 20000:
            raise ValueError("Commands must be ask/steer with 1–20,000 characters")
        with self.db:
            cur = self.db.execute(
                "INSERT INTO commands(run_id,kind,text,created) VALUES(?,?,?,?)",
                (run_id, kind, message, time.time()),
            )
            self._message(
                run_id,
                f"command-{cur.lastrowid}",
                "you",
                "Steering instruction" if kind == "steer" else "Message to MB",
                message,
            )
        return cur.lastrowid

    def commands(self, run_id: str, *, pending_only: bool = False) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM commands WHERE run_id=?"
                + (" AND answer IS NULL" if pending_only else "")
                + " ORDER BY id",
                (run_id,),
            )
        ]

    def pending_questions(self, run_id: str) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM commands WHERE run_id=? AND kind='ask' AND answer IS NULL",
            (run_id,),
        ).fetchone()[0]

    def recent_dialogue(self, run_id: str) -> list[dict]:
        return [
            dict(row)
            for row in reversed(
                self.db.execute(
                    "SELECT role,text FROM chat_messages WHERE run_id=? AND role IN ('you','MB') ORDER BY id DESC LIMIT 8",
                    (run_id,),
                ).fetchall()
            )
        ]

    def answer(self, run_id: str, command: dict, answer: str):
        with self.transaction():
            self.db.execute("UPDATE commands SET answer=? WHERE id=?", (answer, command["id"]))
            self._message(
                run_id,
                f"answer-{command['id']}",
                "MB",
                "Updated brief" if command["kind"] == "steer" else "Reply",
                answer,
            )
            if command["kind"] == "steer":
                self.db.execute(
                    "UPDATE runs SET brief=?,revision=revision+1 WHERE id=?", (answer, run_id)
                )

    def get_limits(self, provider: str) -> dict:
        row = self.db.execute("SELECT data FROM limits WHERE provider=?", (provider,)).fetchone()
        return json.loads(row[0]) if row else {}

    def set_limits(self, provider: str, data: dict):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO limits VALUES(?,?)", (provider, json.dumps(data))
            )
