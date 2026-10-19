"""Offline regression tests; no model, MySQL or RAGFlow calls."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.event_store import EventStore
from app.api import server
from app.api.context import set_thread_context, set_session_context, reset_session_context
from app.api.monitor import monitor


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
        server.active_tasks.clear()
        server.manager.active_connections.clear()
        server.manager.send_locks.clear()
        server.manager.loop = None
        monitor.set_websocket_manager(server.manager)

    def tearDown(self):
        self.patcher.stop()
        self.identity.stop()
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
        async def fake_agent(query, thread_id):
            server._publish_event(thread_id, "session_created", "directory", {"path": "test/path"})
            server._publish_event(thread_id, "task_result", "done", {"result": query + "完成"})

        with patch.object(server, "run_deep_agent", fake_agent), TestClient(server.app) as client:
            response = client.post("/api/task", json={"query": "测试", "thread_id": "a"})
            self.assertEqual(response.status_code, 200)
            # A second request lets the background task finish without a connected socket.
            client.get("/openapi.json")
            with client.websocket_connect("/ws/a") as ws:
                history = ws.receive_json()
                self.assertEqual(history["type"], "event_history")
                self.assertFalse(history["is_running"])
                self.assertEqual([e["event"] for e in history["events"]],
                                 ["task_started", "session_created", "task_result"])
                ws.send_text("ping")
                self.assertEqual(ws.receive_json()["type"], "pong")
            with client.websocket_connect("/ws/a") as ws:
                self.assertEqual(ws.receive_json()["events"], history["events"])
            with client.websocket_connect("/ws/b") as ws:
                self.assertEqual(ws.receive_json()["events"], [])

    def test_running_refresh_and_cancellation(self):
        async def fake_agent(query, thread_id):
            await asyncio.Event().wait()

        with patch.object(server, "run_deep_agent", fake_agent), TestClient(server.app) as client:
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
        async def fake_agent(query, thread_id):
            await asyncio.Event().wait()

        with patch.object(server, "run_deep_agent", fake_agent), TestClient(server.app) as client:
            with client.websocket_connect("/ws/a") as ws:
                self.assertEqual(ws.receive_json()["events"], [])
                client.post("/api/task", json={"query": "q", "thread_id": "a"})
                event = ws.receive_json()
                self.assertEqual(event["event"], "task_started")
                self.assertEqual(event["id"], self.store.history("a")[0]["id"])
                client.post("/api/task/a/cancel")
                ws.receive_json()


if __name__ == "__main__":
    unittest.main()
