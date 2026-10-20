"""Offline regression tests; no model, MySQL or RAGFlow calls."""

import asyncio
import os
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.event_store import EventStore
from app.api import server
from app.api.context import set_thread_context, set_session_context, reset_session_context
from app.api.monitor import monitor
from app.worker import QueueWorker


def successful_queue_attempt(db_path, job):
    queue = server.QueueStore(EventStore(Path(db_path)))
    queue.append_attempt_event(job, {"type": "monitor_event", "event": "task_result",
        "timestamp": "2026-10-20T10:00:00", "message": "done", "data": {"result": "answer"}})


class EventPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.temp.name) / "events.sqlite3")
        self.patcher = patch.object(server.manager, "store", self.store)
        self.patcher.start()
        server.app.dependency_overrides[server.current_user] = lambda: "test-owner"
        self.identity = patch.object(server, "identify", return_value="test-owner")
        self.identity.start()
        for thread in ("a", "b", "offline"):
            server.ensure_thread(self.store, thread, "test-owner", create=True)
        self.environment = patch.dict(os.environ, {"TASK_QUEUE_AUTOSTART": "0"})
        self.environment.start()
        server.manager.active_connections.clear()
        server.manager.send_locks.clear()
        server.manager.loop = None
        monitor.set_websocket_manager(server.manager)

    def tearDown(self):
        self.patcher.stop()
        self.identity.stop()
        self.environment.stop()
        server.app.dependency_overrides.clear()
        self.temp.cleanup()

    def test_order_isolation_and_disk_reopen(self):
        first = server._publish_event("a", "task_started", "started", {"query": "问题"})
        server._publish_event("b", "task_started", "other")
        last = server._publish_event("a", "task_result", "done", {"result": "答案"})
        reopened = EventStore(self.store.path)
        self.assertEqual([e["id"] for e in reopened.history("a")], [first["id"], last["id"]])
        self.assertEqual(reopened.history("a")[-1]["data"]["result"], "答案")
        self.assertEqual(reopened.history("unknown"), [])
        self.assertEqual(reopened.unfinished_threads(), ["b"])

    def test_monitor_persists_without_websocket(self):
        session_token = set_session_context("test/path")
        thread_token = set_thread_context("offline")
        try:
            monitor.report_tool("离线工具", {"query": "测试"})
        finally:
            reset_session_context(session_token, thread_token)
        history = self.store.history("offline")
        self.assertEqual(history[0]["event"], "tool_start")
        self.assertEqual(history[0]["data"]["args"], {"query": "测试"})

    def test_disconnected_task_replays_result_and_heartbeat(self):
        with TestClient(server.app) as client:
            response = client.post("/api/task", json={"query": "测试", "thread_id": "a"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "queued")
            queue = server.QueueStore(self.store)
            job = queue.claim(server.app.state.queue_settings)
            for event, data in (("session_created", {"path": "test/path"}), ("task_result", {"result": "测试完成"})):
                queue.append_attempt_event(job, {"type": "monitor_event", "event": event,
                    "message": "test", "timestamp": "2026-10-20T10:00:00", "data": data})
            queue.finish(job)
            with client.websocket_connect("/ws/a") as ws:
                history = ws.receive_json()
                self.assertEqual(history["type"], "event_history")
                self.assertFalse(history["is_running"])
                self.assertEqual([e["event"] for e in history["events"]],
                                 ["task_queued", "task_running", "session_created", "task_result", "task_completed"])
                ws.send_text("ping")
                self.assertEqual(ws.receive_json()["type"], "pong")
            with client.websocket_connect("/ws/a") as ws:
                self.assertEqual(ws.receive_json()["events"], history["events"])
            with client.websocket_connect("/ws/b") as ws:
                self.assertEqual(ws.receive_json()["events"], [])

    def test_running_refresh_and_cancellation(self):
        with TestClient(server.app) as client:
            client.post("/api/task", json={"query": "running", "thread_id": "a"})
            with client.websocket_connect("/ws/a") as ws:
                self.assertTrue(ws.receive_json()["is_running"])
            with client.websocket_connect("/ws/a") as ws:
                self.assertTrue(ws.receive_json()["is_running"])
                response = client.post("/api/task/a/cancel")
                self.assertEqual(response.json()["status"], "cancelled")
                self.assertEqual(ws.receive_json()["event"], "task_cancelled")
            with client.websocket_connect("/ws/a") as ws:
                history = ws.receive_json()
                self.assertFalse(history["is_running"])
                self.assertEqual(history["events"][-1]["event"], "task_cancelled")

    def test_restart_marks_unfinished_task_interrupted(self):
        server._publish_event("a", "task_started", "started")
        server._publish_event("a", "tool_start", "search")
        server._publish_event("b", "task_result", "done")
        with TestClient(server.app) as client:
            with client.websocket_connect("/ws/a") as ws:
                history = ws.receive_json()
                self.assertFalse(history["is_running"])
                self.assertEqual(history["events"][-1]["event"], "error")
                self.assertIn("中断", history["events"][-1]["message"])
        self.assertEqual(self.store.history("b")[-1]["event"], "task_result")

    def test_replay_precedes_live_events(self):
        with TestClient(server.app) as client:
            with client.websocket_connect("/ws/a") as ws:
                self.assertEqual(ws.receive_json()["events"], [])
                client.post("/api/task", json={"query": "q", "thread_id": "a"})
                event = ws.receive_json()
                self.assertEqual(event["event"], "task_queued")
                self.assertEqual(event["id"], self.store.history("a")[0]["id"])
                client.post("/api/task/a/cancel")
                ws.receive_json()

    def test_queue_status_duplicate_submit_and_manual_failed_retry(self):
        with TestClient(server.app) as client:
            response = client.post("/api/task", json={"query": "q", "thread_id": "a"})
            first_id = response.json()["job_id"]
            self.assertEqual(client.get("/api/task/a").json()["status"], "queued")
            self.assertEqual(client.post("/api/task", json={"query": "duplicate", "thread_id": "a"}).status_code, 409)
            self.assertEqual(client.post("/api/task/a/retry").status_code, 409)
            queue = server.queue_store()
            with self.store._connect() as conn:
                conn.execute("UPDATE jobs SET max_retries=0 WHERE id=?", (first_id,))
            job = queue.claim(server.app.state.queue_settings)
            queue.finish(job, "simulated failure")
            self.assertEqual(client.get("/api/task/a").json()["status"], "failed")
            response = client.post("/api/task/a/retry")
            self.assertEqual(response.status_code, 200)
            self.assertNotEqual(response.json()["job_id"], first_id)
            self.assertEqual(client.get("/api/task/a").json()["status"], "queued")

    def test_queued_job_survives_api_restart(self):
        with TestClient(server.app) as client:
            job_id = client.post("/api/task", json={"query": "survive", "thread_id": "a"}).json()["job_id"]
        with TestClient(server.app) as client:
            state = client.get("/api/task/a").json()
            self.assertEqual(state["job_id"], job_id)
            self.assertEqual(state["status"], "queued")
            with client.websocket_connect("/ws/a") as ws:
                snapshot = ws.receive_json()
                self.assertTrue(snapshot["is_running"])
                self.assertEqual(snapshot["task"]["status"], "queued")

    def test_separate_process_events_reach_websocket_once_in_order(self):
        with TestClient(server.app) as client:
            client.post("/api/task", json={"query": "cross process", "thread_id": "a"})
            worker = QueueWorker(server.queue_store(), target=successful_queue_attempt)
            try:
                with client.websocket_connect("/ws/a") as ws:
                    self.assertEqual(ws.receive_json()["task"]["status"], "queued")
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        worker.tick()
                        if server.queue_store().status("a")["status"] == "succeeded":
                            break
                        time.sleep(0.03)
                    self.assertEqual(server.queue_store().status("a")["status"], "succeeded")
                    events = [ws.receive_json() for _ in range(3)]
                    self.assertEqual([event["event"] for event in events], ["task_running", "task_result", "task_completed"])
                    self.assertEqual(len({event["id"] for event in events}), 3)
                    ws.send_text("ping")
                    self.assertEqual(ws.receive_json()["type"], "pong")
            finally:
                worker.close()

    def test_auto_worker_is_restarted_and_stopped_with_api(self):
        children = []
        original = server.spawn_worker

        def record_worker():
            process = original()
            children.append(process)
            return process

        with patch.dict(os.environ, {"TASK_QUEUE_AUTOSTART": "1"}), patch.object(server, "spawn_worker", side_effect=record_worker):
            with TestClient(server.app):
                deadline = time.monotonic() + 5
                while not children and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(children)
                time.sleep(0.3)
                self.assertIsNone(children[0].poll())
                children[0].kill()
                children[0].wait(timeout=5)
                while len(children) < 2 and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual(len(children), 2)
                self.assertIsNone(children[-1].poll())
        self.assertIsNotNone(children[-1].poll())


if __name__ == "__main__":
    unittest.main()
