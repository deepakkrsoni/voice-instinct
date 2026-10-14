"""SQLite registry of Claude sessions, the tasks given to them, hook events and questions."""

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

from loguru import logger

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY, name TEXT, cwd TEXT,
  origin TEXT NOT NULL DEFAULT 'external', mode TEXT NOT NULL DEFAULT 'interactive',
  state TEXT NOT NULL DEFAULT 'running', tmux_name TEXT,
  started_at REAL NOT NULL, ended_at REAL, end_reason TEXT);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
  request TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running',
  created_at REAL NOT NULL, finished_at REAL, summary TEXT);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
  kind TEXT NOT NULL, payload_json TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS questions(
  id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
  kind TEXT NOT NULL, tool_name TEXT, tool_input_json TEXT, input_hash TEXT NOT NULL,
  prompt TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', answer TEXT,
  asked_at REAL NOT NULL, answered_at REAL);
CREATE TABLE IF NOT EXISTS conversation(
  id INTEGER PRIMARY KEY CHECK (id = 1), messages_json TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS conversation_bad(
  id INTEGER PRIMARY KEY, messages_json TEXT NOT NULL, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS events_by_session ON events(session_id, at);
CREATE INDEX IF NOT EXISTS questions_by_status ON questions(status);
"""
_SESSION_FIELDS = ("name", "cwd", "origin", "mode", "state", "tmux_name")


class Registry:
    def __init__(self, path=":memory:", clock=time.time):
        self.clock = clock
        if path != ":memory:":
            parent = Path(path).parent
            parent.mkdir(parents=True, exist_ok=True)
            os.chmod(parent, 0o700)  # events hold every hook payload: commands, file contents
        # One event loop writes; check_same_thread off so tests can read from another thread.
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        if path != ":memory:":
            os.chmod(path, 0o600)

    def _one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def upsert_session(self, session_id, **fields):
        self.db.execute("INSERT OR IGNORE INTO sessions(id, started_at) VALUES(?, ?)",
                        (session_id, self.clock()))
        given = {k: v for k, v in fields.items() if v is not None and k in _SESSION_FIELDS}
        if given:
            cols = ", ".join(f"{k}=?" for k in given)
            self.db.execute(f"UPDATE sessions SET {cols} WHERE id=?", (*given.values(), session_id))

    def get_session(self, session_id):
        return self._one("SELECT * FROM sessions WHERE id=?", (session_id,))

    def set_state(self, session_id, state, end_reason=None):
        if state == "ended":
            self.db.execute("UPDATE sessions SET state=?, ended_at=?, end_reason=? WHERE id=?",
                            (state, self.clock(), end_reason, session_id))
        else:
            self.db.execute("UPDATE sessions SET state=? WHERE id=?", (state, session_id))

    def add_event(self, session_id, kind, payload):
        cur = self.db.execute(
            "INSERT INTO events(session_id, kind, payload_json, at) VALUES(?,?,?,?)",
            (session_id, kind, json.dumps(payload), self.clock()))
        return cur.lastrowid

    def create_task(self, session_id, request):
        cur = self.db.execute("INSERT INTO tasks(session_id, request, created_at) VALUES(?,?,?)",
                              (session_id, request, self.clock()))
        return cur.lastrowid

    def get_task(self, task_id):
        return self._one("SELECT * FROM tasks WHERE id=?", (task_id,))

    def save_conversation(self, messages):
        """Nova's one ongoing conversation (conversation.py decides what is saved)."""
        self.db.execute("INSERT INTO conversation(id, messages_json, updated_at) VALUES(1,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET messages_json=excluded.messages_json, "
                        "updated_at=excluded.updated_at", (json.dumps(messages), self.clock()))

    def load_conversation(self):
        row = self._one("SELECT messages_json FROM conversation WHERE id=1")
        if row is None:
            return []
        try:
            messages = json.loads(row["messages_json"])
            if isinstance(messages, list):
                return messages
        except ValueError:
            pass
        logger.warning("saved conversation is unreadable: starting a new one (kept in conversation_bad)")
        self.db.execute("INSERT INTO conversation_bad(messages_json, at) VALUES(?,?)",
                        (row["messages_json"], self.clock()))
        return []

    def finish_task(self, task_id, status, summary=""):
        self.db.execute("UPDATE tasks SET status=?, summary=?, finished_at=? WHERE id=?",
                        (status, summary, self.clock(), task_id))

    def add_question(self, session_id, kind, prompt, tool_name=None, tool_input=None):
        blob = json.dumps(tool_input, sort_keys=True)
        h = hashlib.sha1(blob.encode()).hexdigest()
        dup = self._one("SELECT id FROM questions WHERE session_id=? AND kind=? AND status='open' "
                        "AND IFNULL(tool_name,'')=? AND input_hash=?",
                        (session_id, kind, tool_name or "", h))
        if dup:
            return dup["id"]
        cur = self.db.execute(
            "INSERT INTO questions(session_id, kind, tool_name, tool_input_json, input_hash, prompt, asked_at) "
            "VALUES(?,?,?,?,?,?,?)", (session_id, kind, tool_name, blob, h, prompt, self.clock()))
        return cur.lastrowid

    def get_question(self, qid):
        return self._one("SELECT * FROM questions WHERE id=?", (qid,))

    def resolve_question(self, qid, status, answer=None):
        self.db.execute("UPDATE questions SET status=?, answer=?, answered_at=? WHERE id=?",
                        (status, answer, self.clock(), qid))

    def open_questions(self):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM questions WHERE status='open' ORDER BY id")]

    def expire_open(self):
        return self.db.execute(
            "UPDATE questions SET status='expired', answered_at=? WHERE status='open'",
            (self.clock(),)).rowcount

    def list_sessions(self, active_only=False):
        where = "WHERE s.state != 'ended'" if active_only else ""
        rows = self.db.execute(f"""
            SELECT s.*,
              (SELECT COUNT(*) FROM questions q WHERE q.session_id=s.id AND q.status='open') AS open_questions,
              (SELECT request FROM tasks t WHERE t.session_id=s.id ORDER BY t.id DESC LIMIT 1) AS last_task
            FROM sessions s {where} ORDER BY s.started_at DESC""")
        return [dict(r) for r in rows]
