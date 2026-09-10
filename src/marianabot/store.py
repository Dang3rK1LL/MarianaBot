"""Durable checkpoints, user mailbox, and call history. No credentials are stored."""

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from marianabot.config import Config


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
        """)
        self.db.commit()

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
        if not problem.strip() or len(problem) > 20000:
            raise ValueError("The problem must contain 1–20,000 characters")
        run_id = uuid.uuid4().hex[:12]
        with self.db:
            self.db.execute(
                "INSERT INTO runs(id,problem,config,demo,created,status) VALUES(?,?,?,?,?,?)",
                (run_id, problem, config.model_dump_json(), demo, time.time(), "ready"),
            )
        return run_id

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
            self.db.execute(
                "UPDATE calls SET state=?,result=? WHERE id=?",
                (state, json.dumps(result) if result is not None else None, call_id),
            )

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

    def rounds(self, run_id: str) -> list[dict]:
        return [
            dict(r) | {"review": json.loads(r["review"])}
            for r in self.db.execute(
                "SELECT * FROM rounds WHERE run_id=? ORDER BY number", (run_id,)
            )
        ]

    def enqueue(self, run_id: str, kind: str, message: str) -> int:
        run = self.run(run_id)
        if run["status"] in ("complete", "stopped"):
            raise ValueError("This run is closed; create a new run using the exported plan")
        if kind not in ("ask", "steer") or not message.strip() or len(message) > 20000:
            raise ValueError("Commands must be ask/steer with 1–20,000 characters")
        with self.db:
            cur = self.db.execute(
                "INSERT INTO commands(run_id,kind,text,created) VALUES(?,?,?,?)",
                (run_id, kind, message, time.time()),
            )
        return cur.lastrowid

    def commands(self, run_id: str) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute("SELECT * FROM commands WHERE run_id=? ORDER BY id", (run_id,))
        ]

    def answer(self, run_id: str, command: dict, answer: str):
        with self.transaction():
            self.db.execute("UPDATE commands SET answer=? WHERE id=?", (answer, command["id"]))
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
