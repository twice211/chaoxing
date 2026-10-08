"""Desktop bridge checks without external services or the user's configuration."""
from __future__ import annotations

import json
import io
import logging
import queue
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from config import Config
from database.store import Store
from ui.web_bridge import BridgeApi, DesktopScheduler


class WebBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = Config()
        self.cfg.values.update({
            "DB_PATH": str(self.root / "data.db"),
            "AI_API_KEY": "test-private-credential",
            "AI_ENABLE": True,
        })
        self.events = queue.Queue()
        self.scheduler = DesktopScheduler(self.cfg, self.events)
        self.scheduler.ready.set()
        self.api = BridgeApi(self.cfg, scheduler=self.scheduler)

    def tearDown(self) -> None:
        self.api._close()
        self.temp.cleanup()

    def test_bootstrap_never_returns_key_or_creates_missing_database(self) -> None:
        result = self.api.bootstrap()
        self.assertTrue(result["ok"])
        self.assertNotIn(self.cfg.ai_api_key, json.dumps(result))
        self.assertTrue(result["data"]["ai"]["configured"])
        self.assertEqual(result["data"]["courses"], [])
        self.assertFalse(self.cfg.db_path.exists())

    def test_snapshot_reads_saved_selection_and_items_without_private_urls(self) -> None:
        store = Store(self.cfg.db_path)
        cid = store.upsert_course({"course_key": "fixture", "name": "电路分析",
                                   "url": "https://example.invalid/private-course"})
        store.set_meta("current_course", cid)
        store.exec("INSERT INTO items(course_id,item_key,title,kind,done,progress) VALUES(?,?,?,?,?,?)",
                   (cid, "fixture-video", "电路基础", "video", 1, 1))
        store.close()
        snapshot = self.api.bootstrap()["data"]
        self.assertEqual(snapshot["course_id"], cid)
        self.assertEqual(snapshot["courses"], [{"id": cid, "name": "电路分析"}])
        self.assertEqual(snapshot["sections"][0]["title"], "电路基础")
        self.assertTrue(snapshot["sections"][0]["done"])
        self.assertEqual(snapshot["stats"], {"total": 1, "finished": 1})
        self.assertNotIn("private-course", json.dumps(snapshot))
        self.assertEqual(snapshot["login_state"], "unknown")

    def test_confirmed_login_is_visible_in_snapshot_and_survives_course_refresh_failure(self) -> None:
        self.scheduler.browser = SimpleNamespace(
            wait_for_manual_login=lambda **kwargs: True, _cookie_logged_in=lambda: True,
            save_cookies=lambda: 3,
        )
        with patch.object(self.scheduler, "_ensure_browser"), \
                patch.object(self.scheduler, "do_courses", side_effect=RuntimeError("fixture refresh failure")):
            with self.assertRaises(RuntimeError):
                self.scheduler.do_login()
        self.assertEqual(self.api.bootstrap()["data"]["login_state"], "signed_in")
        self.assertFalse(self.scheduler.login_pending.is_set())

    def test_interrupted_login_returns_unverified_state_instead_of_remaining_waiting(self) -> None:
        self.scheduler.browser = SimpleNamespace(
            wait_for_manual_login=Mock(side_effect=RuntimeError("fixture closed browser")),
        )
        with patch.object(self.scheduler, "_ensure_browser"):
            with self.assertRaises(RuntimeError):
                self.scheduler.do_login()
        self.assertEqual(self.api.bootstrap()["data"]["login_state"], "unknown")
        self.assertFalse(self.scheduler.login_pending.is_set())

    def test_explicit_logout_clears_confirmed_login_status(self) -> None:
        self.scheduler.login_state = "signed_in"
        self.scheduler.browser = SimpleNamespace(logout=lambda: None)
        with patch.object(self.scheduler, "_forget_courses_local", return_value=0):
            self.scheduler.do_logout()
        self.assertEqual(self.api.bootstrap()["data"]["login_state"], "signed_out")

    def test_commands_are_queued_instead_of_running_browser_on_api_thread(self) -> None:
        result = self.api.command("play", {"item_id": 17})
        self.assertTrue(result["ok"])
        self.assertEqual(self.scheduler.jobs.get_nowait(), ("play", {"item_id": 17}))

    def test_invalid_commands_and_parameters_are_rejected_without_enqueue(self) -> None:
        cases = [
            ("__getattribute__", {}), ("shutdown", {}),
            ("play", {"item_id": True}), ("play", {"item_id": -1}),
            ("play", {"item_id": "17"}), ("play", {"item_id": 1, "url": "x"}),
            ("auto", {"submit": "false"}), ("auto_chain", {"limit": 0}),
            ("discuss_preview", {"kind": "other"}),
            ("discuss_auto_confirm", {"token": 1, "approved": "yes"}),
            ("search", {"text": "x" * 20001}),
            ("save_settings", {"values": {"AI_MAX_RETRY": -1}}),
            ("save_settings", {"values": {"AI_TIMEOUT_SEC": "abc"}}),
            ("save_settings", {"values": {"AI_ENABLE": "false"}}),
            ("save_settings", {"values": {"UNRECOGNIZED_FIELD": "value"}}),
        ]
        for action, params in cases:
            with self.subTest(action=action, params=params):
                self.assertFalse(self.api.command(action, params)["ok"])
        self.assertTrue(self.scheduler.jobs.empty())

    def test_cancel_interrupts_waiting_login_and_task_before_dispatch(self) -> None:
        self.scheduler.login_pending.set()
        self.api.command("cancel", {})
        self.assertTrue(self.scheduler.login_cancel.is_set())
        self.assertTrue(self.scheduler.task_stop.is_set())
        self.assertEqual(self.scheduler.jobs.get_nowait()[0], "cancel")

    def test_empty_key_input_preserves_saved_key_and_existing_settings(self) -> None:
        target = self.root / "user_config.py"
        with patch("utils.settings_io.config_path", return_value=target):
            self.assertTrue(self.api.command("save_settings", {"values": {
                "AI_API_KEY": "", "AI_MODEL": "fixture-model", "AI_ENABLE": False,
            }})["ok"])
            self.scheduler._pump_once()
        self.assertEqual(self.cfg.get("AI_API_KEY"), "test-private-credential")
        self.assertEqual(self.cfg.ai_model, "fixture-model")
        self.assertFalse(self.cfg.get("AI_ENABLE"))
        self.assertIn("test-private-credential", target.read_text(encoding="utf-8"))
        self.assertNotIn("test-private-credential", json.dumps(self.api.poll()))

    def test_clearing_key_requires_explicit_operation(self) -> None:
        target = self.root / "user_config.py"
        with patch("utils.settings_io.config_path", return_value=target):
            self.assertTrue(self.api.command("save_settings", {
                "values": {}, "clear_api_key": True,
            })["ok"])
            self.scheduler._pump_once()
        self.assertEqual(self.cfg.get("AI_API_KEY"), "")
        self.assertFalse(self.api.bootstrap()["data"]["ai"]["configured"])

    def test_events_redact_secrets_in_text_and_structured_payload(self) -> None:
        secret = self.cfg.ai_api_key
        self.events.put(("err", "Provider failure: " + secret))
        self.events.put(("discuss_confirm", {"token": 2, "text": "draft " + secret}))
        response = self.api.poll()
        self.assertNotIn(secret, json.dumps(response))
        self.assertEqual(response["data"]["events"][1]["payload"]["token"], 2)

    def test_setting_replacement_redacts_both_old_and_new_keys(self) -> None:
        target = self.root / "user_config.py"
        with patch("utils.settings_io.config_path", return_value=target):
            self.api.command("save_settings", {"values": {"AI_API_KEY": "new-private-credential"}})
            self.scheduler._pump_once()
        self.events.put(("err", "test-private-credential new-private-credential"))
        payload = json.dumps(self.api.poll())
        self.assertNotIn("test-private-credential", payload)
        self.assertNotIn("new-private-credential", payload)

    def test_discussion_confirmation_token_and_boolean_are_preserved(self) -> None:
        self.assertTrue(self.api.command("discuss_auto_confirm", {
            "token": 27, "approved": False,
        })["ok"])
        self.assertEqual(self.scheduler.jobs.get_nowait(),
                         ("discuss_auto_confirm", {"token": 27, "approved": False}))

    def test_initialization_error_and_busy_state_are_visible(self) -> None:
        self.scheduler.error = "initialization failed"
        self.scheduler.login_pending.set()
        result = self.api.bootstrap()["data"]
        self.assertEqual(result["initialization_error"], "initialization failed")
        self.assertTrue(result["busy"])
        self.assertTrue(result["login_pending"])

    def test_shutdown_persists_browser_state_on_owning_thread(self) -> None:
        from browser.driver import Browser

        self.scheduler.browser = Mock(spec=Browser)
        store = Mock()
        self.scheduler.app = SimpleNamespace(store=store)
        with patch("ui.scheduler.Scheduler.run"):
            self.scheduler.run()
        self.scheduler.browser.stop.assert_called_once()
        store.close.assert_called_once()

    def test_blocking_command_reports_busy_until_it_finishes(self) -> None:
        entered, release = threading.Event(), threading.Event()
        def blocking():
            entered.set()
            release.wait(3)
        self.scheduler.do_ai_test = blocking
        self.api.command("ai_test", {})
        worker = threading.Thread(target=self.scheduler._pump_once)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(self.api.bootstrap()["data"]["busy"])
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(self.api.bootstrap()["data"]["busy"])

    def test_provider_exception_is_redacted_before_console_and_file_logging(self) -> None:
        from utils.logger import get_logger

        output = io.StringIO()
        handler = logging.StreamHandler(output)
        logger = get_logger()
        logger.addHandler(handler)
        api = BridgeApi(self.cfg, scheduler=self.scheduler)
        try:
            with patch("ai.client.AIClient.ask", side_effect=RuntimeError("provider echoed test-private-credential")):
                api.command("ai_test", {})
                self.scheduler._pump_once()
            self.assertNotIn("test-private-credential", output.getvalue())
            self.assertIn("provider echoed", output.getvalue())
        finally:
            api._close()
            logger.removeHandler(handler)

    def test_temporary_database_failure_preserves_confirmation_and_save_events(self) -> None:
        self.events.put(("settings_saved", "saved"))
        self.events.put(("discuss_confirm", {"token": 3, "text": "fixture batch"}))
        with patch.object(self.api, "_snapshot", side_effect=sqlite3.OperationalError("temporarily locked")):
            self.assertFalse(self.api.poll()["ok"])
        retry = self.api.poll()
        self.assertEqual([event["level"] for event in retry["data"]["events"]],
                         ["settings_saved", "discuss_confirm"])


if __name__ == "__main__":
    unittest.main()
