"""Offline regressions for desktop AI waits and browser thread ownership."""
from __future__ import annotations

import queue
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai.responder import Answer
from config import Config
from questions.models import Question
from ui.web_bridge import DesktopScheduler


def eventually(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.01)
    return False


class AsyncAITests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        self.cfg.values.update(AI_ENABLE=True, AI_API_KEY="offline-test-key")
        self.out = queue.Queue()
        self.s = DesktopScheduler(self.cfg, self.out)
        self.entered, self.release = threading.Event(), threading.Event()
        self.writes, self.pauses, self.ai_threads = [], [], []
        store = SimpleNamespace(get_meta=lambda *a: "", close=lambda: None,
                                upsert_question=lambda *a, **k: (1, False),
                                set_question_ai_result=lambda *a, **k: self.writes.append(threading.get_ident()))
        self.app = SimpleNamespace(store=store, need_kb=lambda: SimpleNamespace(
            evidence_for_question=lambda *a, **k: {}))
        self.build = patch("main.build_app", return_value=self.app)
        self.build.start()
        self.s.course = {"id": 1, "name": "Offline course"}
        self.s.session = SimpleNamespace(done=False, tick=lambda: "playing", progress=0,
                                         notice="", pause=lambda: self.pauses.append(threading.get_ident()),
                                         stop=lambda: None)

    def tearDown(self):
        self.release.set()
        self.s.shutdown()
        if self.s.ident:
            self.s.join(2)
        self.build.stop()

    def blocked(self, *args, **kwargs):
        self.ai_threads.append(threading.get_ident())
        self.entered.set()
        self.release.wait(3)
        return "正常"

    def outcomes(self):
        return [p for level, p in list(self.out.queue) if level == "request_finished"]

    def test_ai_test_keeps_video_pause_responsive_and_ignores_cancelled_reply(self):
        with patch("ai.client.AIClient.ask", side_effect=self.blocked):
            rid = self.s.submit_request("ai_test")
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            self.s.submit_request("pause")
            self.assertTrue(eventually(lambda: bool(self.pauses), .5), "AI wait blocked video pause")
            self.assertEqual(self.pauses, [self.s.ident])
            self.assertNotEqual(self.ai_threads[0], self.s.ident)
            self.s.submit_request("cancel")
            self.assertTrue(eventually(lambda: any(p["request_id"] == rid and p["status"] == "cancelled"
                                                for p in self.outcomes()), .5))
            self.release.set()
            time.sleep(.1)
            self.assertFalse(any(level == "ok" and "AI 连接成功" in str(p) for level, p in self.out.queue))

    def setup_parse(self):
        q = Question.from_raw({"no": "1", "stem": "水的化学式是什么？", "kind": "single",
                               "options": [{"label": "A", "text": "H2O"}, {"label": "B", "text": "CO2"}]})
        def answer(*args, **kwargs):
            self.blocked()
            return Answer(answer="A", analysis="offline", needs_human=False)
        self.s.engine = SimpleNamespace(ai=SimpleNamespace(enabled=True), answer=answer)
        self.s.practice = SimpleNamespace(extractor=SimpleNamespace(extract=lambda p: [q]))
        self.s._ensure_ai = lambda: None
        self.s._ensure_browser = lambda: SimpleNamespace(url="https://offline.local")

    def test_cancelled_parse_never_persists_or_emits_late_answer(self):
        self.setup_parse()
        rid = self.s.submit_request("parse")
        self.s.start()
        self.assertTrue(self.entered.wait(2))
        self.s.submit_request("cancel")
        self.assertTrue(eventually(lambda: any(p["request_id"] == rid and p["status"] == "cancelled"
                                            for p in self.outcomes()), .5), "AI wait blocked cancellation")
        self.release.set()
        time.sleep(.1)
        self.assertEqual(self.writes, [])
        self.assertFalse(any(level == "ai" for level, p in self.out.queue))

    def test_parse_completion_persists_on_scheduler_thread(self):
        self.setup_parse()
        self.s.submit_request("parse")
        self.s.start()
        self.assertTrue(self.entered.wait(2))
        self.release.set()
        self.assertTrue(eventually(lambda: bool(self.writes)))
        self.assertEqual(self.writes, [self.s.ident])
        self.assertNotEqual(self.ai_threads[0], self.s.ident)

    def test_ai_test_shutdown_returns_before_network_finishes(self):
        with patch("ai.client.AIClient.ask", side_effect=self.blocked):
            self.s.submit_request("ai_test")
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            self.s.shutdown()
            self.s.join(.5)
            self.assertFalse(self.s.is_alive(), "Shutdown waited on blocked network")
            self.assertFalse(self.release.is_set())

    def finish_video_during_ai(self):
        plays = []
        session = self.s.session
        session.status = "playing"
        def tick():
            if self.entered.is_set():
                session.done = True
                session.status = "done"
            return session.status
        session.tick = tick
        self.s.auto_next = True
        self.s._next_unfinished = lambda: {"id": 2}
        self.s._refresh_row = lambda: None
        self.s.do_play = lambda item_id: plays.append(item_id)
        return plays

    def test_video_auto_next_waits_for_preserved_ai_task_then_resumes(self):
        plays = self.finish_video_during_ai()
        with patch("ai.client.AIClient.ask", side_effect=self.blocked):
            self.s.submit_request("ai_test")
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            self.assertTrue(eventually(lambda: self.s.session is None))
            self.assertEqual(plays, [])
            self.release.set()
            self.assertTrue(eventually(lambda: plays == [2]), "Automatic progression was lost during AI wait")

    def test_cancelled_ai_does_not_resume_deferred_auto_next(self):
        plays = self.finish_video_during_ai()
        with patch("ai.client.AIClient.ask", side_effect=self.blocked):
            self.s.submit_request("ai_test")
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            self.assertTrue(eventually(lambda: self.s.session is None))
            self.s.submit_request("cancel")
            self.assertTrue(eventually(lambda: self.s.task is None))
            self.release.set()
            time.sleep(.1)
            self.assertEqual(plays, [])

    def test_discussion_partials_are_applied_on_scheduler_and_cancel_token_stays_set(self):
        renders, token_after_cancel = [], []
        def generate(acts, ctx, course, on_partial, should_stop, **kwargs):
            on_partial([{"text": "first"}], 2)
            self.entered.set()
            self.release.wait(3)
            token_after_cancel.append(should_stop())
            on_partial([{"text": "late"}], 2)
            return [{"text": "late"}]
        self.s.discuss = SimpleNamespace(generate_drafts=generate, last_errors=[])
        self.s._discuss_render = lambda *a, **k: renders.append(threading.get_ident())
        self.s.do_discuss_preview = lambda kind="reply": self.s._start_task(
            self.s._discuss_preview_gen(kind, [{"type": "reply", "topic_key": "1"}], {}, "offline"))
        self.s.submit_request("discuss_preview", kind="reply")
        self.s.start()
        self.assertTrue(self.entered.wait(2))
        self.assertTrue(eventually(lambda: bool(renders)))
        self.assertEqual(set(renders), {self.s.ident}, "Worker mutated/rendered scheduler drafts")
        self.s.submit_request("cancel")
        self.assertTrue(eventually(lambda: self.s.task is None))
        self.s.task_stop.clear()  # A newly accepted task must not revive old background work.
        before = list(self.s._discuss_drafts["reply"])
        self.release.set()
        self.assertTrue(eventually(lambda: bool(token_after_cancel)))
        self.assertEqual(token_after_cancel, [True])
        self.assertEqual(self.s._discuss_drafts["reply"], before)

    def test_ai_failure_finishes_request_as_failed(self):
        with patch("ai.client.AIClient.ask", side_effect=RuntimeError("offline unavailable")):
            rid = self.s.submit_request("ai_test")
            self.s.start()
            self.assertTrue(eventually(lambda: any(p["request_id"] == rid for p in self.outcomes())))
            outcome = next(p for p in self.outcomes() if p["request_id"] == rid)
            self.assertEqual(outcome["status"], "failed")
            self.assertIn("offline unavailable", outcome["message"])

    def test_blocked_vision_captures_screenshot_on_owner_thread_and_can_cancel(self):
        from questions.extractor import QuestionExtractor
        from utils import visocr
        captures = []
        raw = [{"no": "1", "stem": "加密题干", "kind": "single", "options": ["A. 水", "B. 火"]}]
        class Frame:
            url = "https://offline.local/encrypted"
            def evaluate(frame, script, *args):
                captures.append(threading.get_ident())
                return raw if script != visocr.MARK_LCA_JS and script != visocr.UNMARK_JS else True
            def query_selector(frame, selector):
                return SimpleNamespace(screenshot=lambda: captures.append(threading.get_ident()) or b"fake-png")
        extractor = QuestionExtractor(self.cfg)
        self.s.engine = SimpleNamespace(ai=SimpleNamespace(enabled=False))
        self.s.practice = SimpleNamespace(extractor=extractor)
        self.s._ensure_ai = lambda: None
        self.s._ensure_browser = lambda: SimpleNamespace(url=Frame.url, frames=[Frame()])
        with patch("questions.extractor.cxfont.build_decoder", return_value=lambda s: s), \
                patch("ai.client.AIClient.ask_vision", side_effect=self.blocked):
            rid = self.s.submit_request("parse")
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            self.s.submit_request("cancel")
            self.assertTrue(eventually(lambda: any(p["request_id"] == rid and p["status"] == "cancelled"
                                                for p in self.outcomes()), .5), "Vision AI blocked cancellation")
            self.assertEqual(set(captures), {self.s.ident})
            self.assertNotEqual(self.ai_threads[0], self.s.ident)

    def test_popup_wait_is_responsive_and_late_manual_answer_is_not_overwritten(self):
        from questions.popup import PopupWatcher
        self.setup_parse()
        q = self.s.practice.extractor.extract(None)[0]
        self.app.store.log_study = lambda *a: None
        popup = PopupWatcher(self.cfg, self.app.store, self.s.engine, course_id=1)
        page = SimpleNamespace(url="https://offline.local/video")
        live, fills = [q], []
        popup.extractor = SimpleNamespace(extract=lambda p: list(live))
        popup._writable = lambda *a, **k: True
        popup._auto_answer_quiz = lambda *a: fills.append(threading.get_ident())
        popup.on_out = lambda level, text: self.s._emit((level, text))
        self.s.popup = popup
        self.s.do_parse = lambda: self.s._start_task(self.s._popup_gen(q, page), preserve_session=True)
        with patch("browser.quiz.extract_items", return_value=[]):
            self.s.submit_request("parse")
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            self.s.submit_request("pause")
            self.assertTrue(eventually(lambda: bool(self.pauses), .5))
            live.clear()  # The user answered manually while the model was waiting.
            self.release.set()
            self.assertTrue(eventually(lambda: self.s.task is None))
            self.assertEqual(fills, [])
            self.assertEqual(self.writes, [], "Stale popup was persisted")

    def test_popup_completion_rechecks_permission_and_applies_only_on_owner(self):
        from questions.popup import PopupWatcher
        self.setup_parse()
        q = self.s.practice.extractor.extract(None)[0]
        self.app.store.log_study = lambda *a: None
        popup = PopupWatcher(self.cfg, self.app.store, self.s.engine, course_id=1)
        page, fills = SimpleNamespace(url="https://offline.local/video"), []
        popup.extractor = SimpleNamespace(extract=lambda p: [q])
        permissions = []
        popup._writable = lambda *a, **k: permissions.append(threading.get_ident()) or True
        popup._auto_answer_quiz = lambda *a: fills.append(threading.get_ident())
        self.s.popup = popup
        self.s.do_parse = lambda: self.s._start_task(self.s._popup_gen(q, page), preserve_session=True)
        with patch("browser.quiz.extract_items", return_value=[]):
            self.s.submit_request("parse")
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            self.release.set()
            self.assertTrue(eventually(lambda: bool(fills)))
            self.assertEqual(fills, [self.s.ident])
            self.assertEqual(self.writes, [self.s.ident])
            self.assertEqual(set(permissions), {self.s.ident})
            self.assertGreaterEqual(len(permissions), 4)

    def test_video_popup_check_defers_ai_and_keeps_pause_responsive(self):
        from questions.popup import PopupWatcher
        self.setup_parse()
        q = self.s.practice.extractor.extract(None)[0]
        self.app.store.log_study = lambda *a: None
        popup = PopupWatcher(self.cfg, self.app.store, self.s.engine, course_id=1)
        popup.on_check = self.s._queue_popup_check
        popup._writable = lambda *a, **k: False
        self.s.popup = popup
        page = SimpleNamespace(url="https://offline.local/video")
        self.s.session.tick = lambda: popup.check(page) or "playing"
        with patch("browser.quiz.extract_items", return_value=[q.as_dict()]):
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            self.s.submit_request("pause")
            self.assertTrue(eventually(lambda: bool(self.pauses), .5), "Video popup AI blocked pause")
            self.assertEqual(self.pauses, [self.s.ident])
            self.s.submit_request("cancel")
            self.assertTrue(eventually(lambda: self.s.task is None, .5))
            self.release.set()
            time.sleep(.1)
            self.assertEqual(self.writes, [])

    def test_popup_failed_live_page_check_never_falls_through_to_autofill(self):
        from questions.popup import PopupWatcher
        self.setup_parse()
        q = self.s.practice.extractor.extract(None)[0]
        self.app.store.log_study = lambda *a: None
        popup = PopupWatcher(self.cfg, self.app.store, self.s.engine, course_id=1)
        popup._writable = lambda *a, **k: True
        fills = []
        popup._auto_answer_quiz = lambda *a: fills.append("unexpected autofill")
        popup.on_out = lambda level, text: self.s._emit((level, text))
        self.s.popup = popup
        page = SimpleNamespace(url="https://offline.local/video")
        self.s.do_parse = lambda: self.s._start_task(self.s._popup_gen(q, page), preserve_session=True)
        with patch("browser.quiz.extract_items", side_effect=RuntimeError("page closed")):
            rid = self.s.submit_request("parse")
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            self.release.set()
            self.assertTrue(eventually(lambda: self.s.task is None))
            self.assertEqual(fills, [])
            self.assertEqual(self.writes, [])
            self.assertEqual(next(p for p in self.outcomes() if p["request_id"] == rid)["status"], "failed")

    def test_auto_browser_apply_occurs_on_scheduler_after_ai_finishes(self):
        self.setup_parse()
        page = SimpleNamespace(url="https://offline.local/practice")
        self.s._ensure_browser = lambda: page
        self.s._writable = lambda *a: True
        applied = []
        snap = SimpleNamespace(apply_practice_actions=lambda *a: applied.append(threading.get_ident()) or {"ok": True})
        with patch("exam.snapshot.ExamSnapshot", return_value=snap):
            self.s.submit_request("auto")
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            self.assertEqual(applied, [])
            self.release.set()
            self.assertTrue(eventually(lambda: bool(applied)))
            self.assertEqual(applied, [self.s.ident])

    def test_auto_uses_fresh_selectors_when_controls_rerender_with_same_question(self):
        self.setup_parse()
        q = self.s.practice.extractor.extract(None)[0]
        q.options[0]["id"] = "old-a"
        live = [q]
        self.s.practice.extractor.extract = lambda p: list(live)
        self.s._writable = lambda *a: True
        applied = []
        snap = SimpleNamespace(apply_practice_actions=lambda p, acts: applied.extend(acts) or {"ok": True})
        with patch("exam.snapshot.ExamSnapshot", return_value=snap):
            self.s.submit_request("auto")
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            replacement = Question.from_raw({**q.as_dict(), "options": [dict(o) for o in q.options]})
            replacement.options[0]["id"] = "new-a"
            live[:] = [replacement]
            self.release.set()
            self.assertTrue(eventually(lambda: bool(applied)))
            self.assertEqual(applied[0]["selector"], "[id='new-a']")

    def test_popup_uses_fresh_question_controls_after_ai_wait(self):
        from questions.popup import PopupWatcher
        self.setup_parse()
        q = self.s.practice.extractor.extract(None)[0]
        q.options[0]["id"] = "old-a"
        live = [q]
        self.app.store.log_study = lambda *a: None
        popup = PopupWatcher(self.cfg, self.app.store, self.s.engine, course_id=1)
        popup.extractor = SimpleNamespace(extract=lambda p: list(live))
        popup._writable = lambda *a, **k: True
        applied = []
        from questions.autofill import plan_answer_actions
        popup._auto_answer_quiz = lambda p, current, ans, submit: applied.extend(plan_answer_actions(current, ans.answer))
        self.s.popup = popup
        self.s.do_parse = lambda: self.s._start_task(self.s._popup_gen(q, SimpleNamespace(url="offline")), preserve_session=True)
        with patch("browser.quiz.extract_items", return_value=[]):
            self.s.submit_request("parse")
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            replacement = Question.from_raw({**q.as_dict(), "options": [dict(o) for o in q.options]})
            replacement.options[0]["id"] = "new-a"
            live[:] = [replacement]
            self.release.set()
            self.assertTrue(eventually(lambda: bool(applied)))
            self.assertEqual(applied[0]["selector"], "[id='new-a']")

    def test_vision_refreshes_dom_control_ids_after_ocr_wait(self):
        import json
        from questions.extractor import QuestionExtractor
        from utils import visocr
        raw = [{"no": "1", "stem": "水的化学式是什么？", "kind": "single",
                "options": [{"label": "A", "text": "H2O", "id": "old-a"}, {"label": "B", "text": "CO2"}]}]
        self.s.engine = SimpleNamespace(ai=SimpleNamespace(enabled=False))
        self.s.practice = SimpleNamespace(extractor=QuestionExtractor(self.cfg))
        self.s._ensure_ai = lambda: None
        class Frame:
            url = "https://offline.local/ocr-controls"
            def evaluate(frame, script, *args):
                return json.loads(json.dumps(raw)) if script not in (visocr.MARK_LCA_JS, visocr.UNMARK_JS) else True
            def query_selector(frame, selector):
                return SimpleNamespace(screenshot=lambda: b"offline-png")
        self.s._ensure_browser = lambda: SimpleNamespace(url=Frame.url, frames=[Frame()])
        captured = []
        self.s._parse_gen = lambda questions: iter(captured.extend(questions) or [])
        def vision(*a, **k):
            self.entered.set()
            self.release.wait(2)
            return json.dumps([{"no": "1", "stem": raw[0]["stem"], "kind": "single", "options": ["A. H2O", "B. CO2"]}])
        with patch("questions.extractor.cxfont.build_decoder", return_value=lambda s: s), \
                patch("ai.client.AIClient.ask_vision", side_effect=vision):
            self.s.submit_request("parse")
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            raw[0]["options"][0]["id"] = "new-a"
            self.release.set()
            self.assertTrue(eventually(lambda: bool(captured)))
            self.assertEqual(captured[0].options[0]["id"], "new-a")

    def assert_stale_ocr_discarded(self, change_frame_url):
        import json
        from questions.extractor import QuestionExtractor
        from utils import visocr
        original = {"no": "1", "stem": "水的化学式是什么？", "kind": "single", "options": ["A. H2O", "B. CO2"]}
        raw = [dict(original)]
        captured = []
        class Frame:
            url = "https://offline.local/stale-ocr-" + ("navigation" if change_frame_url else "question")
            def evaluate(frame, script, *args):
                return json.loads(json.dumps(raw)) if script not in (visocr.MARK_LCA_JS, visocr.UNMARK_JS) else True
            def query_selector(frame, selector):
                return SimpleNamespace(screenshot=lambda: b"offline-png")
        frame = Frame()
        original_url = frame.url
        self.s.engine = SimpleNamespace(ai=SimpleNamespace(enabled=False))
        self.s.practice = SimpleNamespace(extractor=QuestionExtractor(self.cfg))
        self.s._ensure_ai = lambda: None
        self.s._ensure_browser = lambda: SimpleNamespace(url="outer-url-stays-stable", frames=[frame])
        self.s._parse_gen = lambda questions: iter(captured.extend(questions) or [])
        def vision(*a, **k):
            self.entered.set()
            self.release.wait(2)
            return json.dumps([original])
        with patch("questions.extractor.cxfont.build_decoder", return_value=lambda s: s), \
                patch("ai.client.AIClient.ask_vision", side_effect=vision):
            self.s.submit_request("parse")
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            if change_frame_url:
                frame.url = original_url + "/navigated"
            else:
                raw[0]["stem"] = "火焰呈什么颜色？"
            self.release.set()
            self.assertTrue(eventually(lambda: self.s.task is None))
            self.assertEqual(captured, [], "Stale OCR was merged after iframe/question replacement")
            self.assertFalse(any(key.startswith(original_url) for key in visocr._CACHE), "Stale OCR entered cache")

    def test_vision_discards_result_after_iframe_navigation(self):
        self.assert_stale_ocr_discarded(True)

    def test_vision_discards_result_after_question_content_changes(self):
        self.assert_stale_ocr_discarded(False)

    def test_auto_navigation_while_ai_pending_drops_old_page_result(self):
        self.setup_parse()
        page = SimpleNamespace(url="https://offline.local/practice")
        self.s._ensure_browser = lambda: page
        self.s._writable = lambda *a: True
        applied = []
        snap = SimpleNamespace(apply_practice_actions=lambda *a: applied.append(threading.get_ident()) or {"ok": True})
        with patch("exam.snapshot.ExamSnapshot", return_value=snap):
            self.s.submit_request("auto")
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            page.url = "https://offline.local/different-practice"
            self.release.set()
            self.assertTrue(eventually(lambda: self.s.task is None))
            self.assertEqual(applied, [], "Old answer was applied to a newly navigated page")

    def test_auto_question_replacement_while_ai_pending_is_not_filled(self):
        self.setup_parse()
        page = SimpleNamespace(url="https://offline.local/practice")
        self.s._ensure_browser = lambda: page
        self.s._writable = lambda *a: True
        original = self.s.practice.extractor.extract(None)
        live = list(original)
        self.s.practice.extractor.extract = lambda p: list(live)
        applied = []
        snap = SimpleNamespace(apply_practice_actions=lambda *a: applied.append(threading.get_ident()) or {"ok": True})
        with patch("exam.snapshot.ExamSnapshot", return_value=snap):
            self.s.submit_request("auto")
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            live.clear()
            self.release.set()
            self.assertTrue(eventually(lambda: self.s.task is None))
            self.assertEqual(applied, [], "Old answer was applied after DOM questions changed")

    def test_ai_wait_does_not_speed_up_video_heartbeat(self):
        ticks = []
        self.s.session.tick = lambda: ticks.append(time.monotonic()) or "playing"
        with patch("ai.client.AIClient.ask", side_effect=self.blocked):
            self.s.submit_request("ai_test")
            self.s.start()
            self.assertTrue(self.entered.wait(2))
            time.sleep(.3)
            self.assertLessEqual(len(ticks), 1, "AI polling accelerated video heartbeat")


if __name__ == "__main__":
    unittest.main()
