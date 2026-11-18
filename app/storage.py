from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                    mentor_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_owner ON sessions(owner);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                    role TEXT NOT NULL, content TEXT NOT NULL, resources TEXT NOT NULL DEFAULT '[]',
                    citations TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'complete',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, id);
            ''')
        path.chmod(0o600)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create(self, owner: str, mentor_id: str | None = None):
        session = dict(id=uuid.uuid4().hex, owner=owner, title='新的对话', mentor_id=mentor_id,
                       created_at=now(), updated_at=now())
        with self.connect() as db:
            db.execute('INSERT INTO sessions VALUES (:id,:owner,:title,:mentor_id,:created_at,:updated_at)', session)
        return {k: v for k, v in session.items() if k != 'owner'}

    def list(self, owner: str):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,title,mentor_id,updated_at FROM sessions WHERE owner=? ORDER BY updated_at DESC LIMIT 100', (owner,))]

    def session(self, session_id: str, owner: str):
        with self.connect() as db:
            row = db.execute('SELECT id,title,mentor_id,created_at,updated_at FROM sessions WHERE id=? AND owner=?', (session_id, owner)).fetchone()
            return dict(row) if row else None

    def messages(self, session_id: str):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM messages WHERE session_id=? ORDER BY id', (session_id,)).fetchall()
        return [{**dict(row), 'resources': json.loads(row['resources']), 'citations': json.loads(row['citations'])} for row in rows]

    def add(self, session_id: str, role: str, content: str, resources=None, citations=None, status='complete'):
        with self.connect() as db:
            result = db.execute('INSERT INTO messages(session_id,role,content,resources,citations,status,created_at) VALUES(?,?,?,?,?,?,?)',
                                (session_id, role, content, json.dumps(resources or [], ensure_ascii=False), json.dumps(citations or [], ensure_ascii=False), status, now()))
            if role == 'user':
                db.execute("UPDATE sessions SET title=CASE WHEN title='新的对话' THEN ? ELSE title END,updated_at=? WHERE id=?", (content[:28], now(), session_id))
            else:
                db.execute('UPDATE sessions SET updated_at=? WHERE id=?', (now(), session_id))
            return result.lastrowid
