"""Durable local-host queue. Job transitions and UI events commit atomically."""
import datetime
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass

from app.api.event_store import EventStore

ACTIVE = ("queued", "running", "retry_wait", "cancelling")


def env_int(name, default, minimum, maximum):
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class QueueSettings:
    concurrency: int = 2
    timeout: int = 600
    max_retries: int = 3
    retry_delay: int = 2
    lease_seconds: int = 15

    @classmethod
    def from_env(cls):
        return cls(env_int("TASK_QUEUE_CONCURRENCY", 2, 1, 16),
                   env_int("TASK_QUEUE_TIMEOUT_SECONDS", 600, 1, 86400),
                   env_int("TASK_QUEUE_MAX_RETRIES", 3, 0, 10),
                   env_int("TASK_QUEUE_RETRY_DELAY_SECONDS", 2, 0, 300),
                   env_int("TASK_QUEUE_LEASE_SECONDS", 15, 3, 300))


class QueueConflict(ValueError):
    pass


class QueueStore:
    def __init__(self, events: EventStore):
        self.events = events
        with events._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, query TEXT NOT NULL,
                status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL, timeout INTEGER NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                available_at REAL NOT NULL, lease_until REAL, token TEXT,
                last_error TEXT NOT NULL DEFAULT '', retry_of TEXT)""")
            conn.execute("CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(status, available_at, created_at)")
            conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS jobs_active_thread ON jobs(thread_id)
                WHERE status IN ('queued','running','retry_wait','cancelling')""")

    @staticmethod
    def _row(conn, sql, params=()):
        conn.row_factory = sqlite3.Row
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _event(conn, job, event, message, data=None):
        payload = {"type": "monitor_event", "event": event, "message": message,
                   "timestamp": datetime.datetime.now().isoformat(),
                   "data": {"job_id": job["id"], "attempt": job["attempt"], **(data or {})}}
        conn.execute("INSERT INTO events(thread_id,payload) VALUES (?,?)",
                     (job["thread_id"], json.dumps(payload, ensure_ascii=False)))

    def enqueue(self, thread_id, query, settings, retry_of=None, now=None):
        now = time.time() if now is None else now
        job = dict(id=uuid.uuid4().hex, thread_id=thread_id, query=query, status="queued",
                   attempt=0, max_retries=settings.max_retries, timeout=settings.timeout,
                   created_at=now, updated_at=now, available_at=now, retry_of=retry_of)
        with self.events._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM jobs WHERE thread_id=? AND status IN ('queued','running','retry_wait','cancelling')", (thread_id,)).fetchone():
                raise QueueConflict("当前会话已有排队或执行中的任务")
            conn.execute("""INSERT INTO jobs(id,thread_id,query,status,max_retries,timeout,
                created_at,updated_at,available_at,retry_of) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                tuple(job[k] for k in ("id", "thread_id", "query", "status", "max_retries", "timeout", "created_at", "updated_at", "available_at", "retry_of")))
            self._event(conn, job, "task_queued", "任务已进入队列", {"query": query, "status": "queued"})
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversations'").fetchone():
                conn.execute("""UPDATE conversations SET title=CASE WHEN title='' THEN ? ELSE title END,
                    updated_at=CURRENT_TIMESTAMP WHERE thread_id=?""", (query, thread_id))
        return job

    def latest(self, thread_id):
        with self.events._connect() as conn:
            return self._row(conn, "SELECT * FROM jobs WHERE thread_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (thread_id,))

    def get(self, job_id):
        with self.events._connect() as conn:
            return self._row(conn, "SELECT * FROM jobs WHERE id=?", (job_id,))

    def status(self, thread_id):
        job = self.latest(thread_id)
        if not job:
            return {"status": "idle", "is_running": False, "queue_position": None}
        with self.events._connect() as conn:
            ahead = conn.execute("""SELECT count(*) FROM jobs WHERE status IN ('queued','retry_wait')
                AND available_at<=? AND (created_at<? OR (created_at=? AND rowid<
                (SELECT rowid FROM jobs WHERE id=?)))""",
                (time.time(), job["created_at"], job["created_at"], job["id"])).fetchone()[0]
        return {"job_id": job["id"], "status": job["status"], "attempt": job["attempt"],
                "max_retries": job["max_retries"], "timeout_seconds": job["timeout"],
                "last_error": job["last_error"], "is_running": job["status"] in ACTIVE,
                "queue_position": ahead + 1 if job["status"] == "queued" else None}

    def claim(self, settings, now=None):
        now = time.time() if now is None else now
        with self.events._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            count = conn.execute("SELECT count(*) FROM jobs WHERE status IN ('running','cancelling')").fetchone()[0]
            if count >= settings.concurrency:
                return None
            job = self._row(conn, """SELECT * FROM jobs WHERE status IN ('queued','retry_wait')
                AND available_at<=? ORDER BY created_at,rowid LIMIT 1""", (now,))
            if not job:
                return None
            job.update(status="running", attempt=job["attempt"] + 1, token=uuid.uuid4().hex,
                       lease_until=now + settings.lease_seconds, updated_at=now)
            conn.execute("UPDATE jobs SET status=?,attempt=?,token=?,lease_until=?,updated_at=? WHERE id=?",
                         tuple(job[k] for k in ("status", "attempt", "token", "lease_until", "updated_at", "id")))
            self._event(conn, job, "task_running", f"正在执行任务（第 {job['attempt']} 次尝试）", {"status": "running"})
            return job

    def heartbeat(self, job, lease_seconds):
        with self.events._connect() as conn:
            return conn.execute("""UPDATE jobs SET lease_until=?,updated_at=? WHERE id=? AND token=?
                AND status IN ('running','cancelling') AND lease_until>?""",
                (time.time() + lease_seconds, time.time(), job["id"], job["token"], time.time())).rowcount == 1

    def owns(self, job):
        current = self.get(job["id"])
        return bool(current and current["token"] == job["token"] and current["status"] == "running"
                    and current["lease_until"] > time.time())

    def append_attempt_event(self, job, payload):
        with self.events._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._row(conn, "SELECT * FROM jobs WHERE id=?", (job["id"],))
            if not current or current["token"] != job["token"] or current["status"] != "running" or current["lease_until"] <= time.time():
                return None
            payload = {**payload, "data": {**payload.get("data", {}), "job_id": job["id"], "attempt": job["attempt"]}}
            cursor = conn.execute("INSERT INTO events(thread_id,payload) VALUES (?,?)",
                                  (job["thread_id"], json.dumps(payload, ensure_ascii=False)))
            return {**payload, "id": cursor.lastrowid}

    def cancel(self, thread_id):
        with self.events._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = self._row(conn, "SELECT * FROM jobs WHERE thread_id=? AND status IN ('queued','running','retry_wait','cancelling')", (thread_id,))
            if not job:
                return None
            status = "cancelling" if job["status"] in ("running", "cancelling") else "cancelled"
            if job["status"] != status:
                conn.execute("UPDATE jobs SET status=?,updated_at=? WHERE id=?", (status, time.time(), job["id"]))
                self._event(conn, job, "task_cancelling" if status == "cancelling" else "task_cancelled",
                            "正在停止任务" if status == "cancelling" else "任务已取消", {"status": status})
            return status

    def finish(self, job, error=None, settings=None, now=None):
        now = time.time() if now is None else now
        settings = settings or QueueSettings()
        with self.events._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._row(conn, "SELECT * FROM jobs WHERE id=?", (job["id"],))
            if not current or current["token"] != job["token"] or current["status"] not in ("running", "cancelling"):
                return False
            if current["status"] == "cancelling":
                status, event, message = "cancelled", "task_cancelled", "任务已取消"
            elif error is None:
                status, event, message = "succeeded", "task_completed", "任务执行完成"
            elif current["attempt"] <= current["max_retries"]:
                status, event, message = "retry_wait", "task_retrying", "任务失败，等待自动重试"
            else:
                status, event, message = "failed", "error", "任务失败，已达到重试上限"
            available = now + min(60, settings.retry_delay * 2 ** max(0, current["attempt"] - 1))
            conn.execute("""UPDATE jobs SET status=?,last_error=?,available_at=?,updated_at=?,
                token=NULL,lease_until=NULL WHERE id=?""", (status, error or "", available, now, job["id"]))
            self._event(conn, current, event, message + (f"：{error}" if error and status != "cancelled" else ""),
                        {"status": status, "last_error": error or "", "max_retries": current["max_retries"],
                         "available_at": available if status == "retry_wait" else None})
            return True

    def recover_expired(self, settings, now=None):
        now = time.time() if now is None else now
        with self.events._connect() as conn:
            conn.row_factory = sqlite3.Row
            jobs = [dict(row) for row in conn.execute("SELECT * FROM jobs WHERE status IN ('running','cancelling') AND lease_until<=?", (now,))]
        for job in jobs:
            self.finish(job, "worker 心跳已过期，任务将重新执行", settings, now)
        return len(jobs)

    def attempt_result(self, job):
        result = error = None
        for event in self.events.history(job["thread_id"]):
            data = event.get("data", {})
            if data.get("job_id") == job["id"] and data.get("attempt") == job["attempt"]:
                if event["event"] == "task_result":
                    result = data.get("result", event["message"])
                if event["event"] == "attempt_error":
                    error = event["message"]
        return result, error

    def conversation_messages(self, thread_id, before_job):
        with self.events._connect() as conn:
            conn.row_factory = sqlite3.Row
            jobs = [dict(row) for row in conn.execute("""SELECT * FROM jobs WHERE thread_id=?
                AND status='succeeded' AND created_at<=? AND id<>? ORDER BY created_at,rowid""",
                (thread_id, before_job["created_at"], before_job["id"]))]
        messages = []
        legacy_query = None
        for event in self.events.history(thread_id):
            data = event.get("data", {})
            if data.get("job_id"):
                continue
            if event["event"] == "task_started":
                legacy_query = data.get("query")
            elif event["event"] == "task_result" and legacy_query:
                messages.extend([{"role": "user", "content": legacy_query},
                                 {"role": "assistant", "content": data.get("result", event["message"])}])
                legacy_query = None
            elif event["event"] in ("error", "task_cancelled"):
                legacy_query = None
        for job in jobs:
            result, _ = self.attempt_result(job)
            if result is not None:
                messages.extend([{"role": "user", "content": job["query"]}, {"role": "assistant", "content": result}])
        return messages
