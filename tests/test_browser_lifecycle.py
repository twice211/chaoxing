"""Browser lifecycle regressions with an isolated profile and local pages only."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from playwright.sync_api import Error as PlaywrightError

from browser.driver import Browser, LoginRequired
from config import Config


class BrowserLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = Config()
        self.cfg.values.update(USER_DATA_DIR=str(Path(self.temp.name) / "profile"),
                               BROWSER_CDP_PORT=0, LOGIN_POLL_SEC=1, SLOW_MO_MS=0)
        self.browser = Browser(self.cfg, headless=True)
        # Empty fixture enables headless local verification; contains no credentials.
        self.browser.cookie_file().write_text("[]", encoding="utf-8")

    def tearDown(self) -> None:
        self.browser.stop()
        self.temp.cleanup()

    def test_start_reopens_externally_closed_browser_context(self) -> None:
        self.browser.start()
        old_context, old_page = self.browser.context, self.browser.page
        old_context.close()
        self.browser.start()
        self.assertIsNot(self.browser.context, old_context)
        self.assertIsNot(self.browser.page, old_page)
        self.assertFalse(self.browser.page.is_closed())
        self.assertEqual(self.browser.page.evaluate("1 + 1"), 2)

    def test_start_selects_surviving_tab_when_current_tab_was_closed(self) -> None:
        self.browser.start()
        surviving = self.browser.new_tab()
        context = self.browser.context
        self.browser.page.close()
        self.browser.start()
        self.assertIs(self.browser.context, context)
        self.assertIs(self.browser.page, surviving)
        self.assertFalse(self.browser.page.is_closed())

    def test_start_is_idempotent_for_live_context_and_page(self) -> None:
        self.browser.start()
        context, page = self.browser.context, self.browser.page
        self.browser.start()
        self.assertIs(self.browser.context, context)
        self.assertIs(self.browser.page, page)

    def test_closing_browser_during_login_exits_wait_with_actionable_error(self) -> None:
        self.browser.start()
        began = time.monotonic()
        with patch("browser.actions.safe_goto", return_value=True):
            with self.assertRaisesRegex(LoginRequired, "再次.*登录"):
                self.browser.wait_for_manual_login(timeout=4, on_prompt=lambda: self.browser.context.close())
        self.assertLess(time.monotonic() - began, 2)

    def test_navigation_failure_does_not_wait_on_unopened_login_page(self) -> None:
        self.browser.start()
        with patch("browser.actions.safe_goto", return_value=False):
            with self.assertRaisesRegex(LoginRequired, "登录页面"):
                self.browser.wait_for_manual_login(timeout=1)

    def test_closing_browser_mid_poll_interrupts_wait_without_timeout(self) -> None:
        self.browser.start()
        self.browser.page.goto("data:text/html,local-login-fixture")
        checks = 0
        def not_logged_in():
            nonlocal checks
            checks += 1
            if checks == 2:
                self.browser.context.close()
            return False
        began = time.monotonic()
        with patch("browser.actions.safe_goto", return_value=True), \
                patch.object(self.browser, "_cookie_logged_in", side_effect=not_logged_in):
            with self.assertRaisesRegex(LoginRequired, "再次.*登录"):
                self.browser.wait_for_manual_login(timeout=4)
        self.assertLess(time.monotonic() - began, 2)

    def test_failed_launch_releases_playwright_before_next_attempt(self) -> None:
        runtime = Mock()
        runtime.chromium.launch_persistent_context.side_effect = PlaywrightError("fixture launch failure")
        starter = Mock()
        starter.start.return_value = runtime
        with patch("browser.driver.sync_playwright", return_value=starter):
            with self.assertRaises(PlaywrightError):
                self.browser.start()
        runtime.stop.assert_called_once()
        self.assertIsNone(self.browser.pw)
        self.assertIsNone(self.browser.context)

    def test_close_during_real_cookie_check_does_not_reopen_until_next_start(self) -> None:
        self.browser.start()
        context = self.browser.context
        self.browser.page.goto("data:text/html,local-login-fixture")
        real_check = self.browser._cookie_logged_in
        checks = 0
        def close_before_real_check():
            nonlocal checks
            checks += 1
            if checks == 2:
                context.close()
            return real_check()
        with patch("browser.actions.safe_goto", return_value=True), \
                patch.object(self.browser, "_cookie_logged_in", side_effect=close_before_real_check):
            with self.assertRaisesRegex(LoginRequired, "再次.*登录"):
                self.browser.wait_for_manual_login(timeout=2)
        self.assertIs(self.browser.context, context)
        self.assertFalse(self.browser._waiting_login)
        self.browser.start()
        self.assertIsNot(self.browser.context, context)
        self.assertFalse(self.browser.page.is_closed())


if __name__ == "__main__":
    unittest.main()
