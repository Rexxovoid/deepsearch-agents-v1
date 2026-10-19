import re
from pathlib import Path
from fastapi import HTTPException


def validate_thread(thread_id: str):
    if not re.fullmatch(r"[a-z0-9_-]{1,100}", thread_id):
        raise HTTPException(400, "无效的会话")


def ensure_thread(store, thread_id: str, owner: str, create=False):
    validate_thread(thread_id)
    with store._connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS conversations (thread_id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        row = conn.execute("SELECT owner FROM conversations WHERE thread_id=?", (thread_id,)).fetchone()
        if row is None and create:
            # Legacy conversations have no verified owner and cannot be claimed.
            app_root = Path(__file__).resolve().parents[1]
            legacy_files = any((app_root / folder / f"session_{thread_id}").exists() for folder in ("output", "updated"))
            if legacy_files or conn.execute("SELECT 1 FROM events WHERE thread_id=? LIMIT 1", (thread_id,)).fetchone():
                raise HTTPException(403, "该历史会话尚未分配所有者")
            conn.execute("INSERT INTO conversations(thread_id,owner) VALUES (?,?)", (thread_id, owner))
        elif row is None or row[0] != owner:
            raise HTTPException(404, "会话不存在或无权访问")


def name_thread(store, thread_id: str, query: str):
    with store._connect() as conn:
        conn.execute("UPDATE conversations SET title=CASE WHEN title='' THEN ? ELSE title END, updated_at=CURRENT_TIMESTAMP WHERE thread_id=?", (query, thread_id))


def list_threads(store, owner: str):
    with store._connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS conversations (thread_id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        rows = conn.execute("SELECT thread_id,title,updated_at FROM conversations WHERE owner=? AND title<>'' ORDER BY updated_at DESC,rowid DESC", (owner,)).fetchall()
    return [dict(thread_id=r[0], title=r[1], updated_at=r[2]) for r in rows]
