"""Independent queue worker, with one killable process per attempt (Windows-safe)."""
import argparse
import asyncio
import datetime
import multiprocessing
import os
from pathlib import Path
import signal
import threading
import time
from dotenv import load_dotenv

from app.api.event_store import EventStore
from app.task_queue import QueueSettings, QueueStore

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def safe_error(exc):
    message = f"{type(exc).__name__}: {exc}"
    for name, value in os.environ.items():
        if value and len(value) >= 8 and any(word in name.upper() for word in ("KEY", "TOKEN", "PASSWORD", "SECRET")):
            message = message.replace(value, "<已隐藏>")
    return message[:500]


def execute_attempt(db_path, job):
    queue = QueueStore(EventStore(Path(db_path)))
    stopped = threading.Event()

    def watch_lease():
        while not stopped.wait(0.5):
            # Also kills orphaned attempts when their supervisor has crashed.
            if not queue.owns(job):
                os._exit(2)

    watchdog = threading.Thread(target=watch_lease, daemon=True)
    watchdog.start()
    try:
        from app.api.monitor import manager, monitor
        from app.agent.main_agent import run_deep_agent
        manager.store = queue.events
        manager.publish = lambda thread_id, payload: queue.append_attempt_event(job, payload)
        monitor.set_websocket_manager(manager)
        messages = queue.conversation_messages(job["thread_id"], job)
        asyncio.run(run_deep_agent(job["query"], job["thread_id"], history_messages=messages, managed=True))
        result, _ = queue.attempt_result(job)
        if result is None:
            raise RuntimeError("Agent 未产生最终结果")
        queue.append_attempt_event(job, {"type": "monitor_event", "event": "attempt_finished",
            "message": "本次执行已完成", "timestamp": datetime.datetime.now().isoformat(), "data": {}})
    except BaseException as exc:
        queue.append_attempt_event(job, {"type": "monitor_event", "event": "attempt_error",
            "message": safe_error(exc), "timestamp": datetime.datetime.now().isoformat(), "data": {}})
        raise SystemExit(1) from None
    finally:
        stopped.set()


class QueueWorker:
    def __init__(self, store, settings=None, target=execute_attempt):
        self.store = store
        self.settings = settings or QueueSettings.from_env()
        self.target = target
        self.context = multiprocessing.get_context("spawn")
        self.running = {}

    @staticmethod
    def stop_process(process):
        if process.is_alive():
            process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
        if process.is_alive():
            raise RuntimeError("无法终止任务进程，保留租约等待故障恢复")

    def tick(self):
        self.store.recover_expired(self.settings)
        now = time.monotonic()
        for job_id, entry in list(self.running.items()):
            job, process = entry["job"], entry["process"]
            current = self.store.get(job_id)
            if not current or current["token"] != job["token"]:
                self.stop_process(process)
                self.running.pop(job_id)
                continue
            if current["status"] == "cancelling":
                self.stop_process(process)
                self.store.finish(job, settings=self.settings)
                self.running.pop(job_id)
            elif now - entry["started"] >= job["timeout"]:
                self.stop_process(process)
                self.store.finish(job, f"任务执行超时（{job['timeout']} 秒），已终止本次进程", self.settings)
                self.running.pop(job_id)
            elif not process.is_alive():
                process.join()
                result, error = self.store.attempt_result(job)
                if process.exitcode != 0 or result is None:
                    error = error or f"任务进程异常退出（退出码 {process.exitcode}）"
                self.store.finish(job, error, self.settings)
                self.running.pop(job_id)
            elif now - entry["heartbeat"] >= self.settings.lease_seconds / 3:
                if not self.store.heartbeat(job, self.settings.lease_seconds):
                    self.stop_process(process)
                    self.running.pop(job_id)
                entry["heartbeat"] = now
        while len(self.running) < self.settings.concurrency:
            job = self.store.claim(self.settings)
            if job is None:
                break
            process = self.context.Process(target=self.target, args=(str(self.store.events.path), job))
            try:
                process.start()
            except Exception as exc:
                self.store.finish(job, safe_error(exc), self.settings)
                break
            self.running[job["id"]] = {"job": job, "process": process,
                                       "started": time.monotonic(), "heartbeat": time.monotonic()}

    def close(self):
        for entry in list(self.running.values()):
            self.stop_process(entry["process"])
            self.store.finish(entry["job"], "worker 正在退出，任务等待恢复", self.settings)
        self.running.clear()

    def run(self):
        stopping = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stopping.set())
        try:
            while not stopping.is_set():
                self.tick()
                stopping.wait(0.2)
        finally:
            self.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description="Run the durable DeepSearch task worker")
    parser.add_argument("--db", type=Path, default=Path(__file__).parent / "data" / "events.sqlite3")
    args = parser.parse_args()
    QueueWorker(QueueStore(EventStore(args.db))).run()
