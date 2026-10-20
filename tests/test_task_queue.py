"""Queue durability/ownership plus real spawned attempts; no external model calls."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from app.api.event_store import EventStore
from app.task_queue import QueueConflict, QueueSettings, QueueStore
from app.worker import QueueWorker, safe_error


def fake_success(db_path, job):
    queue = QueueStore(EventStore(Path(db_path)))
    queue.append_attempt_event(job, {"type": "monitor_event", "event": "task_result",
        "timestamp": "2026-10-20T10:00:00", "message": "done", "data": {"result": "answer"}})


def fake_crash_once(db_path, job):
    if job["attempt"] == 1:
        raise SystemExit(7)
    fake_success(db_path, job)


def fake_hang(db_path, job):
    time.sleep(60)


class QueueFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.events = EventStore(Path(self.temp.name) / "events.sqlite3")
        self.queue = QueueStore(self.events)
        self.settings = QueueSettings(concurrency=1, timeout=3, max_retries=1, retry_delay=0, lease_seconds=3)

    def tearDown(self):
        self.temp.cleanup()

    def enqueue(self, thread="a", settings=None, now=None):
        return self.queue.enqueue(thread, "question", settings or self.settings, now=now)


class QueueStoreTests(QueueFixture, unittest.TestCase):

    def test_atomic_enqueue_persists_input_and_single_active_thread(self):
        job = self.enqueue()
        reopened = QueueStore(EventStore(self.events.path))
        self.assertEqual(reopened.get(job["id"])["query"], "question")
        self.assertEqual(self.events.history("a")[0]["event"], "task_queued")
        with self.assertRaises(QueueConflict): self.enqueue()
        self.assertEqual(reopened.status("a")["queue_position"], 1)
        with patch.object(self.queue, "_event", side_effect=RuntimeError("rollback")):
            with self.assertRaises(RuntimeError): self.enqueue("b")
        self.assertIsNone(self.queue.latest("b"))

    def test_fifo_concurrency_and_only_one_claimant(self):
        first, second = self.enqueue("a"), self.enqueue("b")
        with ThreadPoolExecutor(4) as pool:
            jobs = list(pool.map(lambda _: self.queue.claim(self.settings), range(4)))
        claimed = [job for job in jobs if job]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["id"], first["id"])
        self.queue.finish(claimed[0])
        self.assertEqual(self.queue.claim(self.settings)["id"], second["id"])

    def test_backoff_retry_cap_and_persistent_failure(self):
        settings = QueueSettings(concurrency=1, max_retries=3, retry_delay=2)
        self.enqueue(settings=settings)
        now = time.time()
        for attempt in range(1, 5):
            job = self.queue.claim(settings, now=now)
            self.assertEqual(job["attempt"], attempt)
            self.queue.finish(job, "failed", settings, now=now)
            state = self.queue.latest("a")
            if attempt <= 3:
                self.assertEqual(state["status"], "retry_wait")
                self.assertEqual(state["available_at"], now + 2 * 2 ** (attempt - 1))
                self.assertIsNone(self.queue.claim(settings, now=state["available_at"] - 0.1))
                now = state["available_at"]
            else:
                self.assertEqual(state["status"], "failed")
        self.assertIsNone(self.queue.claim(settings, now=now + 100))
        self.assertEqual(self.events.history("a")[-1]["event"], "error")

    def test_expired_lease_recovers_and_old_attempt_cannot_write_or_complete(self):
        self.enqueue(now=time.time() - 10)
        old = self.queue.claim(self.settings, now=time.time() - 10)
        self.queue.recover_expired(self.settings)
        self.assertEqual(self.queue.latest("a")["status"], "retry_wait")
        new = self.queue.claim(self.settings)
        self.assertNotEqual(old["token"], new["token"])
        self.assertIsNone(self.queue.append_attempt_event(old, {"event": "task_result", "data": {}}))
        self.assertFalse(self.queue.finish(old))
        self.assertTrue(self.queue.owns(new))

    def test_expired_final_attempt_becomes_failed(self):
        settings = QueueSettings(max_retries=0, lease_seconds=3)
        self.enqueue(settings=settings, now=time.time() - 10)
        self.queue.claim(settings, now=time.time() - 10)
        self.queue.recover_expired(settings)
        self.assertEqual(self.queue.status("a")["status"], "failed")

    def test_cancel_waiting_and_running_and_expired_cancellation(self):
        self.enqueue()
        self.assertEqual(self.queue.cancel("a"), "cancelled")
        self.assertIsNone(self.queue.claim(self.settings))
        self.enqueue("b")
        job = self.queue.claim(self.settings)
        self.assertEqual(self.queue.cancel("b"), "cancelling")
        self.assertFalse(self.queue.owns(job))
        self.queue.finish(job, "worker stopped", self.settings)
        self.assertEqual(self.queue.status("b")["status"], "cancelled")
        self.enqueue("c", now=time.time() - 10)
        self.queue.claim(self.settings, now=time.time() - 10)
        self.queue.cancel("c")
        self.queue.recover_expired(self.settings)
        self.assertEqual(self.queue.status("c")["status"], "cancelled")

    def test_heartbeat_does_not_revive_an_expired_attempt(self):
        self.enqueue(now=time.time() - 10)
        job = self.queue.claim(self.settings, now=time.time() - 10)
        self.assertFalse(self.queue.heartbeat(job, 3))

    def test_completed_context_is_rebuilt_without_failed_attempt_results(self):
        self.enqueue()
        job = self.queue.claim(self.settings)
        fake_success(self.events.path, job)
        self.queue.finish(job)
        self.enqueue()
        next_job = self.queue.claim(self.settings)
        self.assertEqual(self.queue.conversation_messages("a", next_job),
                         [{"role": "user", "content": "question"}, {"role": "assistant", "content": "answer"}])

    def test_error_redacts_secrets(self):
        with patch.dict("os.environ", {"SERVICE_API_KEY": "test-secret-key-value"}):
            self.assertNotIn("test-secret-key-value", safe_error(ValueError("test-secret-key-value")))

    def test_legacy_completed_conversation_is_available_to_new_attempts(self):
        for event, data in (("task_started", {"query": "old question"}), ("task_result", {"result": "old answer"})):
            self.events.append("a", {"event": event, "data": data, "message": "legacy"})
        self.enqueue()
        job = self.queue.claim(self.settings)
        self.assertEqual(self.queue.conversation_messages("a", job),
                         [{"role": "user", "content": "old question"}, {"role": "assistant", "content": "old answer"}])


class QueueProcessTests(QueueFixture, unittest.TestCase):
    def run_until(self, worker, status, deadline=12):
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            worker.tick()
            if self.queue.status("a")["status"] == status:
                return
            time.sleep(0.03)
        self.fail(f"queue did not reach {status}; got {self.queue.status('a')}")

    def test_spawned_worker_completes_a_durable_job(self):
        self.enqueue()
        worker = QueueWorker(self.queue, self.settings, fake_success)
        try:
            self.run_until(worker, "succeeded")
            self.assertEqual(self.queue.status("a")["attempt"], 1)
        finally: worker.close()

    def test_spawned_worker_crash_retries_then_succeeds(self):
        self.enqueue()
        worker = QueueWorker(self.queue, self.settings, fake_crash_once)
        try:
            self.run_until(worker, "succeeded")
            self.assertEqual(self.queue.status("a")["attempt"], 2)
        finally: worker.close()

    def test_hung_process_is_killed_and_final_failure_is_durable(self):
        settings = QueueSettings(concurrency=1, timeout=1, max_retries=0, lease_seconds=3)
        self.enqueue(settings=settings)
        worker = QueueWorker(self.queue, settings, fake_hang)
        try:
            worker.tick()
            process = next(iter(worker.running.values()))["process"]
            self.run_until(worker, "failed")
            self.assertFalse(process.is_alive())
            self.assertIn("超时", self.queue.status("a")["last_error"])
        finally: worker.close()

    def test_cancellation_terminates_running_process(self):
        self.enqueue()
        worker = QueueWorker(self.queue, self.settings, fake_hang)
        try:
            worker.tick()
            process = next(iter(worker.running.values()))["process"]
            self.queue.cancel("a")
            self.run_until(worker, "cancelled")
            self.assertFalse(process.is_alive())
        finally: worker.close()


if __name__ == "__main__": unittest.main()
