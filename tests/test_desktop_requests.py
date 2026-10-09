"""Accepted desktop requests always receive one correlated terminal outcome."""
from __future__ import annotations

import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import Config
from ui.web_bridge import BridgeApi, DesktopScheduler


class DesktopRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = Config()
        self.cfg.values["DB_PATH"] = str(Path(self.temp.name) / "missing.db")
        self.out = queue.Queue()
        self.scheduler = DesktopScheduler(self.cfg, self.out)
        self.api = BridgeApi(self.cfg, scheduler=self.scheduler)

    def tearDown(self):
        self.api._close()
        self.temp.cleanup()

    def request(self, action, params=None):
        result = self.api.command(action, params)
        self.assertTrue(result["ok"])
        self.assertIsInstance(result.get("request_id"), str)
        self.assertTrue(result["request_id"])
        return result["request_id"]

    def finished(self):
        return [event["payload"] for event in self.api.poll()["data"]["events"]
                if event["level"] == "request_finished"]

    def assert_outcome(self, results, request_id, action, status):
        matches = [result for result in results if result["request_id"] == request_id]
        self.assertEqual(len(matches), 1, results)
        self.assertEqual(matches[0]["action"], action)
        self.assertEqual(matches[0]["status"], status)
        self.assertIsInstance(matches[0]["message"], str)

    def test_starting_long_task_preserves_accepted_settings_command(self):
        def task():
            yield None
            yield None
        self.scheduler.do_auto = lambda submit=False: self.scheduler._start_task(task())
        auto = self.api.command("auto")
        save = self.api.command("save_settings", {"values": {"AI_MODEL": "queued-model"}})
        self.scheduler._pump_once()
        self.assertEqual(self.scheduler.jobs.qsize(), 1, "Starting a task discarded accepted settings")
        self.scheduler._pump_once()
        results = self.finished()
        self.assert_outcome(results, save["request_id"], "save_settings", "rejected")
        self.assertFalse(any(result["request_id"] == auto["request_id"] for result in results))

    def test_success_and_failure_are_correlated_and_unique(self):
        observed = []
        self.scheduler.do_auto_next = lambda on: observed.append(on)
        first = self.request("auto_next", {"on": True})
        second = self.request("auto_next", {"on": False})
        self.assertNotEqual(first, second)
        self.scheduler._pump_once()
        self.scheduler._pump_once()
        self.assertEqual(observed, [True, False], "Metadata leaked into command arguments")
        results = self.finished()
        self.assert_outcome(results, first, "auto_next", "succeeded")
        self.assert_outcome(results, second, "auto_next", "succeeded")
        with patch.object(self.scheduler, "do_save_settings", side_effect=OSError("fixture denied")):
            failed = self.request("save_settings")
            self.scheduler._pump_once()
        self.assert_outcome(self.finished(), failed, "save_settings", "failed")

    def test_handled_command_error_is_not_reported_as_success(self):
        self.cfg.values["AI_ENABLE"] = False
        request_id = self.request("ai_test")
        self.scheduler._pump_once()
        self.assert_outcome(self.finished(), request_id, "ai_test", "failed")

    def test_idle_conflict_does_not_dispatch_and_finishes_as_rejected(self):
        def task():
            yield None
            yield None
        self.scheduler._start_task(task())
        request_id = self.request("play", {"item_id": 1})
        self.scheduler._pump_once()
        self.assert_outcome(self.finished(), request_id, "play", "rejected")
        self.assertIsNotNone(self.scheduler.task)

    def test_long_request_only_finishes_when_generator_ends(self):
        def task():
            yield None
            yield None
        self.scheduler.do_auto = lambda submit=False: self.scheduler._start_task(task())
        request_id = self.request("auto")
        self.scheduler._pump_once()
        self.assertEqual(self.finished(), [])
        self.scheduler._step_task()
        self.assertEqual(self.finished(), [])
        self.scheduler._step_task()
        self.assert_outcome(self.finished(), request_id, "auto", "succeeded")
        self.scheduler._step_task()
        self.assertEqual(self.finished(), [])

    def test_cancel_accepted_before_long_task_starts_prevents_first_task_step(self):
        steps = []
        def task():
            steps.append("side effect")
            yield None
        self.scheduler.do_auto = lambda submit=False: self.scheduler._start_task(task())
        request_id = self.request("auto")
        cancel_id = self.request("cancel")
        self.scheduler._pump_once()
        self.assertEqual(steps, [], "Task start cleared a newer cancellation")
        self.scheduler._pump_once()
        results = self.finished()
        self.assert_outcome(results, request_id, "auto", "cancelled")
        self.assert_outcome(results, cancel_id, "cancel", "succeeded")

    def test_long_failure_and_cancel_are_correlated_and_close_generator(self):
        closed = []
        def task():
            try:
                yield from self.scheduler._wait(999)
                yield None  # Even a generator without a cooperative check must stop.
            finally:
                closed.append(threading.get_ident())
        self.scheduler.do_auto = lambda submit=False: self.scheduler._start_task(task())
        request_id = self.request("auto")
        self.scheduler._pump_once()
        cancel_id = self.request("cancel")
        self.scheduler._pump_once()
        results = self.finished()
        self.assert_outcome(results, request_id, "auto", "cancelled")
        self.assert_outcome(results, cancel_id, "cancel", "succeeded")
        self.assertEqual(closed, [threading.get_ident()])
        self.assertIsNone(self.scheduler.task)
        self.assertFalse(self.scheduler.stop_event.is_set())
        def failed_task():
            yield None
            raise RuntimeError("fixture generator failure")
        self.scheduler.do_auto = lambda submit=False: self.scheduler._start_task(failed_task())
        failed_id = self.request("auto")
        self.scheduler._pump_once()
        self.scheduler._step_task()
        self.assert_outcome(self.finished(), failed_id, "auto", "failed")

    def test_new_long_request_after_cancel_can_start(self):
        steps = []
        def task():
            steps.append("new task")
            yield None
        self.scheduler.do_auto = lambda submit=False: self.scheduler._start_task(task())
        cancel_id = self.request("cancel")
        request_id = self.request("auto")
        self.scheduler._pump_once()
        self.scheduler._pump_once()
        self.assertEqual(steps, ["new task"])
        self.scheduler._step_task()
        results = self.finished()
        self.assert_outcome(results, cancel_id, "cancel", "succeeded")
        self.assert_outcome(results, request_id, "auto", "succeeded")

    def test_cancel_does_not_resume_started_generator_into_side_effects(self):
        effects, cleanup = [], []
        def task():
            try:
                yield None
                effects.append("write")
                yield None
            finally:
                cleanup.append(threading.get_ident())
        self.scheduler.do_auto = lambda submit=False: self.scheduler._start_task(task())
        request_id = self.request("auto")
        self.scheduler._pump_once()
        cancel_id = self.request("cancel")
        self.scheduler._pump_once()
        self.assertEqual(effects, [], "Cancellation resumed post-yield writes")
        self.assertEqual(cleanup, [threading.get_ident()])
        self.assertFalse(self.scheduler.stop_event.is_set())
        results = self.finished()
        self.assert_outcome(results, request_id, "auto", "cancelled")
        self.assert_outcome(results, cancel_id, "cancel", "succeeded")

    def test_shutdown_cancels_queued_requests_and_does_not_run_them(self):
        observed = []
        self.scheduler.do_auto_next = lambda on: observed.append(on)
        ids = [self.request("auto_next", {"on": True}) for _ in range(2)]
        self.scheduler.shutdown()
        self.scheduler._pump_once()
        self.assertEqual(observed, [])
        results = self.finished()
        for request_id in ids:
            self.assert_outcome(results, request_id, "auto_next", "cancelled")
        late_id = self.request("auto_next", {"on": False})
        self.assert_outcome(self.finished(), late_id, "auto_next", "cancelled")

    def test_shutdown_closes_long_generator_on_scheduler_thread(self):
        entered, closed = threading.Event(), []
        def task():
            try:
                entered.set()
                yield from self.scheduler._wait(999)
            finally:
                closed.append(threading.get_ident())
        self.scheduler.do_auto = lambda submit=False: self.scheduler._start_task(task())
        request_id = self.request("auto")
        app = SimpleNamespace(need_kb=lambda: None,
                              store=SimpleNamespace(get_meta=lambda *args: "", close=lambda: None))
        with patch("main.build_app", return_value=app):
            self.scheduler.start()
            try:
                self.assertTrue(entered.wait(2))
                self.scheduler.shutdown()
                self.scheduler.join(3)
                self.assertFalse(self.scheduler.is_alive())
            finally:
                self.scheduler.shutdown()
                self.scheduler.join(3)
        self.assertEqual(closed, [self.scheduler.ident])
        self.assert_outcome(self.finished(), request_id, "auto", "cancelled")

    def test_initialization_failure_finishes_already_accepted_requests(self):
        request_id = self.request("pause")
        with patch("main.build_app", side_effect=RuntimeError("fixture startup failure")):
            self.scheduler.run()
        self.assert_outcome(self.finished(), request_id, "pause", "failed")

    def test_cancelled_login_finishes_original_request_as_cancelled(self):
        waiting = threading.Event()
        def wait_for_login(**kwargs):
            waiting.set()
            self.scheduler.login_cancel.wait(2)
            return False
        self.scheduler.browser = SimpleNamespace(wait_for_manual_login=wait_for_login,
                                                  _cookie_logged_in=lambda: False, logout=lambda: None)
        request_id = self.request("login")
        with patch.object(self.scheduler, "_ensure_browser"):
            worker = threading.Thread(target=self.scheduler._pump_once)
            worker.start()
            try:
                self.assertTrue(waiting.wait(2))
                cancel_id = self.request("cancel")
            finally:
                self.scheduler.login_cancel.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.scheduler._pump_once()
        results = self.finished()
        self.assert_outcome(results, request_id, "login", "cancelled")
        self.assert_outcome(results, cancel_id, "cancel", "succeeded")

    def test_legacy_submit_keeps_plain_tuple_queue(self):
        self.scheduler.submit("auto_next", on=True)
        self.assertEqual(self.scheduler.jobs.get_nowait(), ("auto_next", {"on": True}))


if __name__ == "__main__":
    unittest.main()
