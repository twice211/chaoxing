"""Local Chromium integration of the built React UI and the Python bridge.

Browser/platform operations use fixtures; no real AI or platform requests run.
Run after `npm run build` in frontend: python -m unittest tests.test_web_frontend -v.
"""
from __future__ import annotations

import functools
import queue
import re
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright

from config import BASE_DIR, Config
from database.store import Store
from ui.web_bridge import BridgeApi, DesktopScheduler


class QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


class FrontendIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        bundle = BASE_DIR / "frontend" / "dist"
        if not (bundle / "index.html").is_file():
            raise RuntimeError("Build frontend first with npm run build")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                        functools.partial(QuietStaticHandler, directory=str(bundle)))
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(2)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = Config()
        self.cfg.values.update(DB_PATH=str(Path(self.temp.name) / "fixture.db"),
                               AI_API_KEY="local-fixture-credential", AI_MODEL="fixture-model",
                               AI_ENABLE=True, DISCUSS_MAX=5)
        self.store = Store(self.cfg.db_path)
        self.cid = self.store.upsert_course({"course_key": "fixture", "name": "电路分析"})
        self.store.set_meta("current_course", self.cid)
        self.iid = self.store.exec("INSERT INTO items(course_id,item_key,title,kind,progress) VALUES(?,?,?,?,?)",
                                  (self.cid, "fixture-video", "电路基础", "video", 0.5))
        self.docid = self.store.exec("INSERT INTO items(course_id,item_key,title,kind) VALUES(?,?,?,?)",
                                    (self.cid, "fixture-document", "课程阅读资料", "document"))
        self.events = queue.Queue()
        self.scheduler = DesktopScheduler(self.cfg, self.events)
        self.scheduler.app = SimpleNamespace(store=self.store)
        self.scheduler.course = {"id": self.cid}
        self.scheduler.ready.set()
        self.scheduler.kb = SimpleNamespace(search=lambda *a, **k: [],
                                            render_hits=lambda *a, **k: "本地资料：电路中的欧姆定律")
        self.commands = []
        def confirmed_login():
            self.scheduler.login_state = "signed_in"
            self.events.put(("ok", "登录浏览器已打开（本地测试）"))
        self.scheduler.do_login = confirmed_login
        self.scheduler.do_open_item = lambda item_id: self.events.put(("item", "video|电路基础|50%"))
        self.scheduler.do_play = lambda item_id: self.events.put(("playstatus", "电路基础 ｜ 播放中 ｜ 50%"))
        self.scheduler.do_pause = lambda: self.events.put(("info", "播放已暂停"))
        self.scheduler.do_read = lambda item_id, min_seconds=None: self.events.put(("info", "阅读任务已开始"))
        self.scheduler.do_ai_test = lambda: self.events.put(("ok", "AI 连接测试完成（本地模拟）"))
        self.scheduler.do_discuss_auto = lambda kind="new_post": self.events.put(
            ("discuss_confirm", {"token": 27, "text": "测试课程：完整讨论草稿，请确认"}))
        self.scheduler.do_discuss_auto_confirm = lambda token, approved=False: self.events.put(
            ("info", "测试确认已接收"))
        self.api = BridgeApi(self.cfg, scheduler=self.scheduler)
        self.settings_patch = patch("utils.settings_io.config_path", return_value=Path(self.temp.name) / "user_config.py")
        self.settings_patch.start()
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 850})
        self.page = self.context.new_page()
        self.console_errors = []
        self.page.on("pageerror", lambda error: self.console_errors.append(str(error)))
        self.page.expose_function("desktopBootstrap", self.api.bootstrap)
        self.page.expose_function("desktopPoll", self.api.poll)
        self.page.expose_function("desktopCommand", self.command)
        self.page.add_init_script("""window.pywebview = {api: {
          bootstrap: () => window.desktopBootstrap(), poll: () => window.desktopPoll(),
          command: (action, params) => window.desktopCommand(action, params)
        }}""")
        self.page.goto(self.url)
        expect(self.page.locator(".task-status")).to_have_text("就绪")

    def command(self, action: str, params: dict) -> dict:
        result = self.api.command(action, params)
        if result["ok"]:
            self.commands.append((action, params))
            self.scheduler._pump_once()
        return result

    def tearDown(self) -> None:
        self.context.close()
        self.settings_patch.stop()
        self.api._close()
        self.store.close()
        self.temp.cleanup()
        self.assertEqual(self.console_errors, [])

    def navigate(self, name: str) -> None:
        self.page.get_by_role("navigation", name="主导航").get_by_role("button", name=name, exact=True).click()
        expect(self.page.get_by_role("heading", name=name, exact=True, level=1)).to_be_visible()

    def test_study_controls_and_progress_are_wired_to_python_commands(self) -> None:
        self.page.get_by_role("button", name=re.compile("电路基础")).click()
        expect(self.page.locator(".progress-label strong")).to_have_text("50%")
        self.page.get_by_role("button", name="播放本节", exact=True).click()
        self.page.get_by_role("button", name="暂停", exact=True).click()
        self.assertIn(("play", {"item_id": self.iid}), self.commands)
        self.assertIn(("pause", {}), self.commands)

    def test_login_button_dispatches_and_shows_backend_feedback(self) -> None:
        self.store.set_meta("current_course", "")
        self.events.put(("alert", "请在浏览器完成登录"))
        self.page.get_by_role("button", name="登录", exact=True).click()
        self.assertIn(("login", {}), self.commands)
        expect(self.page.get_by_role("log")).to_contain_text("登录浏览器已打开（本地测试）")
        expect(self.page.locator(".task-status")).to_have_text("已登录")
        expect(self.page.get_by_role("button", name="打开学习通", exact=True)).to_be_visible()
        expect(self.page.get_by_text("已登录，读取到 1 门课程", exact=True)).to_be_visible()
        expect(self.page.locator(".error-banner")).to_have_count(0)

    def test_reading_rejects_invalid_duration_then_dispatches_valid_duration(self) -> None:
        self.page.get_by_role("button", name=re.compile("课程阅读资料")).click()
        field = self.page.get_by_label("阅读时长（秒）")
        field.fill("-1")
        self.page.get_by_role("button", name="开始阅读", exact=True).click()
        expect(self.page.get_by_text("阅读时长请输入非负整数秒，留空使用配置。")).to_be_visible()
        field.fill("30")
        self.page.get_by_role("button", name="开始阅读", exact=True).click()
        self.assertIn(("read", {"item_id": self.docid, "min_seconds": 30}), self.commands)

    def test_actual_backend_error_stays_visible_after_login_confirmation(self) -> None:
        self.events.put(("err", "读取课程失败：本地模拟网络故障"))
        self.page.get_by_role("button", name="登录", exact=True).click()
        expect(self.page.locator(".task-status")).to_have_text("已登录")
        expect(self.page.locator(".error-banner")).to_contain_text("读取课程失败：本地模拟网络故障")

    def test_settings_preserve_key_and_external_changes_when_only_model_is_edited(self) -> None:
        self.navigate("设置")
        password = self.page.locator('input[type="password"]')
        expect(password).to_have_value("")
        self.page.get_by_label("模型名", exact=False).fill("fixture-updated")
        self.navigate("课程讨论")
        self.page.get_by_label("最多处理（条）").fill("10")
        self.page.get_by_role("button", name="应用数量", exact=True).click()
        self.navigate("设置")
        expect(self.page.get_by_label("模型名", exact=False)).to_have_value("fixture-updated")
        self.page.get_by_role("button", name="保存全部设置", exact=True).click()
        expect(self.page.get_by_text("设置已保存。")).to_be_visible()
        self.assertEqual(self.cfg.get("AI_API_KEY"), "local-fixture-credential")
        self.assertEqual(self.cfg.get("DISCUSS_MAX"), 10)
        self.assertEqual(self.cfg.ai_model, "fixture-updated")
        self.assertNotIn("local-fixture-credential", self.page.content())

    def test_discussion_confirmation_requires_explicit_approval(self) -> None:
        self.navigate("课程讨论")
        self.page.get_by_role("button", name="开始本轮讨论", exact=True).click()
        dialog = self.page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_text("测试课程：完整讨论草稿，请确认")).to_be_visible()
        self.assertNotIn("discuss_auto_confirm", [action for action, _ in self.commands])
        dialog.get_by_role("button", name="确认发布本轮", exact=True).click()
        expect(dialog).not_to_be_visible()
        self.assertIn(("discuss_auto_confirm", {"token": 27, "approved": True}), self.commands)

    def test_search_results_ignore_unrelated_output(self) -> None:
        self.navigate("资料搜索")
        self.page.get_by_label("搜索关键词").fill("欧姆定律")
        self.page.get_by_role("button", name="搜索资料", exact=True).click()
        expect(self.page.locator(".workspace-pages").get_by_text("本地资料：电路中的欧姆定律", exact=True)).to_be_visible()
        self.events.put(("raw", "unrelated exercise output"))
        expect(self.page.get_by_role("log")).to_contain_text("unrelated exercise output")
        self.assertNotIn("unrelated exercise output", self.page.locator(".workspace-pages").inner_text())

    def test_desktop_widths_do_not_overflow(self) -> None:
        for width in (960, 1280):
            self.page.set_viewport_size({"width": width, "height": 820})
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width)
        self.page.get_by_role("button", name=re.compile("电路基础")).click()
        self.page.screenshot(path=str(BASE_DIR / ".worktrees" / "desktop-preview.png"), full_page=True)


if __name__ == "__main__":
    unittest.main()
