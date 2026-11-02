"""多轮会话存储（SQLite）：会话、消息、槽位。单机自用，无登录。"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from app.config import settings


@contextmanager
def conn():
    cfg = settings()
    cfg["sqlite_path"].parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(cfg["sqlite_path"])
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                slots TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                payload TEXT,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
            """
        )


def create_conversation(title: str = "新会话") -> str:
    cid = uuid.uuid4().hex[:12]
    now = time.time()
    with conn() as c:
        c.execute(
            "INSERT INTO conversations(id, title, slots, created_at, updated_at) VALUES (?,?,?,?,?)",
            (cid, title[:60], None, now, now),
        )
    return cid


def list_conversations(limit: int = 30) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(cid: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
        if not row:
            return None
        msgs = c.execute(
            "SELECT role, content, payload, created_at FROM messages WHERE conversation_id = ? ORDER BY id",
            (cid,),
        ).fetchall()
    out = dict(row)
    out["slots"] = json.loads(out["slots"]) if out["slots"] else None
    out["messages"] = [
        {
            "role": m["role"],
            "content": m["content"],
            "payload": json.loads(m["payload"]) if m["payload"] else None,
            "created_at": m["created_at"],
        }
        for m in msgs
    ]
    return out


def add_message(cid: str, role: str, content: str, payload: dict | None = None) -> None:
    now = time.time()
    with conn() as c:
        c.execute(
            "INSERT INTO messages(conversation_id, role, content, payload, created_at) VALUES (?,?,?,?,?)",
            (cid, role, content, json.dumps(payload, ensure_ascii=False) if payload else None, now),
        )
        c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, cid))


def update_slots(cid: str, slots: dict | None) -> None:
    with conn() as c:
        c.execute(
            "UPDATE conversations SET slots = ?, updated_at = ? WHERE id = ?",
            (json.dumps(slots, ensure_ascii=False) if slots else None, time.time(), cid),
        )


def rename(cid: str, title: str) -> None:
    with conn() as c:
        c.execute("UPDATE conversations SET title = ? WHERE id = ?", (title[:60], cid))
