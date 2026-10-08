from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from config import Config
from ui.web_bridge import BridgeApi
from ui.web_desktop import run_desktop


class WindowEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class DesktopStartupTests(unittest.TestCase):
    def test_raw_webview_dispatch_cannot_traverse_private_api_objects(self) -> None:
        import webview
        from webview.util import js_bridge_call

        cfg = Config()
        cfg.values["AI_API_KEY"] = "private-test-credential"
        api = BridgeApi(cfg)
        window = webview.create_window("Bridge boundary test", html="<div></div>")
        window.expose(api.bootstrap, api.poll, api.command)
        try:
            self.assertIsNone(window._js_api)
            self.assertEqual(set(window._functions), {"bootstrap", "poll", "command"})
            with patch.object(window, "evaluate_js") as evaluate:
                js_bridge_call(window, "_cfg.get", ["AI_API_KEY"], "test")
                js_bridge_call(window, "_scheduler.do_login", [], "test")
                evaluate.assert_not_called()
        finally:
            webview.windows.remove(window)
            api._close()

    def test_window_serves_only_frontend_bundle_and_shuts_down_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "frontend" / "dist" / "index.html"
            bundle.parent.mkdir(parents=True)
            bundle.write_text("<!doctype html><div id='root'></div>", encoding="utf-8")
            closing_event = WindowEvent()
            window = SimpleNamespace(events=SimpleNamespace(closing=closing_event), expose=Mock())
            webview = SimpleNamespace(create_window=Mock(return_value=window), start=Mock())
            scheduler = Mock()
            scheduler.is_alive.return_value = False
            with patch.dict(sys.modules, {"webview": webview}), \
                    patch("ui.web_desktop.DesktopScheduler", return_value=scheduler), \
                    patch("ui.web_desktop.BridgeApi"):
                self.assertEqual(run_desktop(Config(), bundle_path=bundle), 0)
            self.assertEqual(Path(webview.create_window.call_args.kwargs["url"]), bundle)
            self.assertNotIn("js_api", webview.create_window.call_args.kwargs)
            self.assertEqual(len(window.expose.call_args.args), 3)
            self.assertTrue(webview.start.call_args.kwargs["http_server"])
            self.assertTrue(webview.start.call_args.kwargs["private_mode"])
            scheduler.start.assert_called_once()
            scheduler.shutdown.assert_called_once()
            self.assertEqual(len(closing_event.handlers), 1)

    def test_missing_bundle_reports_build_instruction_without_starting_thread(self) -> None:
        with patch("ui.web_desktop._show_error") as show_error, \
                patch("ui.web_desktop.DesktopScheduler") as scheduler:
            self.assertEqual(run_desktop(Config(), bundle_path=Path("absent-bundle/index.html")), 2)
        self.assertIn("npm", show_error.call_args.args[0])
        scheduler.assert_not_called()

    def test_missing_dependency_reports_install_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "index.html"
            bundle.write_text("<div></div>", encoding="utf-8")
            with patch.dict(sys.modules, {"webview": None}), patch("ui.web_desktop._show_error") as show_error:
                self.assertEqual(run_desktop(Config(), bundle_path=bundle), 2)
            self.assertIn("pip", show_error.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
