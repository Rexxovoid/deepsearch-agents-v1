"""Durable, ordered WebSocket events, isolated by conversation ID."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class EventStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL, "
                "payload TEXT NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS events_thread ON events(thread_id, id)")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def append(self, thread_id: str, payload: dict) -> dict:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO events(thread_id, payload) VALUES (?, ?)",
                (thread_id, json.dumps(payload, ensure_ascii=False)),
            )
            return {**payload, "id": cursor.lastrowid}

    def history(self, thread_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, payload FROM events WHERE thread_id = ? ORDER BY id",
                (thread_id,),
            ).fetchall()
        return [{**json.loads(payload), "id": event_id} for event_id, payload in rows]

    def latest_id(self):
        with self._connect() as conn:
            return conn.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]

    def after(self, event_id, limit=200):
        with self._connect() as conn:
            rows = conn.execute("SELECT id,thread_id,payload FROM events WHERE id>? ORDER BY id LIMIT ?", (event_id, limit)).fetchall()
        return [(thread, {**json.loads(payload), "id": item_id}) for item_id, thread, payload in rows]

    def unfinished_threads(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT e.thread_id, e.payload FROM events e JOIN "
                "(SELECT thread_id, MAX(id) AS id FROM events GROUP BY thread_id) latest "
                "ON e.id = latest.id"
            ).fetchall()
        terminal = {"task_result", "task_completed", "task_cancelled", "error"}
        return [thread for thread, payload in rows if json.loads(payload)["event"] not in terminal]


event_store = EventStore(Path(__file__).resolve().parents[1] / "data" / "events.sqlite3")
