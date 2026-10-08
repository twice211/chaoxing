"""讨论发布回归测试：只操作本地 HTML，不连接课程平台。"""
from __future__ import annotations

import io
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config import Config
from course.discussion import DiscussionRunner, content_fp
from exam.guard import ExamGuard
from tests.selftest import temp_store
from ui.workbench import Scheduler


class DiscussionPublishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.cfg = Config()
        self.cfg.values.update(PRACTICE_MODE=True, PRACTICE_ALLOW_DISCUSS_POST=True,
                               PRACTICE_ALLOW_DISCUSS_SUBMIT=True)
        self.storage = temp_store("publish.db")
        self.store, _ = self.storage.__enter__()
        self.cid = self.store.upsert_course({"course_key": "local", "name": "测试课程"})
        self.guard = ExamGuard(self.cfg, self.store)
        self.runner = DiscussionRunner(browser=None, cfg=self.cfg, store=self.store,
                                       crawler=None, engine=None)
        self.draft = {"type": "reply", "topic_key": "abc", "title": "目标话题",
                      "text": "这段回复包含 A < B 与课程概念。", "fp": content_fp("这段回复包含 A < B 与课程概念。")}

    def tearDown(self):
        self.page.close()
        self.storage.__exit__(None, None, None)

    def fixture(self, action=""):
        self.page.set_content('''<div id="toast" role="status"></div>
            <div id="editor" contenteditable="true"></div><div id="published"></div>
            <button id="send" class="replyBtn" onclick="window.clicks++;''' + action + '''">回复</button>
            <script>window.clicks=0</script>''')
        self.assertTrue(self.runner.fill_editor(self.page, self.draft["text"])["body"])

    def scheduler(self):
        sched = Scheduler(self.cfg, queue.Queue())
        sched.course = {"id": self.cid, "name": "测试课程"}
        sched.app = SimpleNamespace(store=self.store)
        sched.guard = self.guard
        sched.discuss = self.runner
        sched._ensure_browser = lambda: self.page
        sched._discuss_drafts["reply"] = [self.draft]
        return sched

    def test_click_without_confirmation_is_pending(self):
        self.fixture()
        state, _ = self.runner.publish(self.page, "reply", self.draft["text"], self.guard, timeout_sec=0.15)
        self.assertEqual(state, "pending_verify")
        self.assertEqual(self.page.evaluate("window.clicks"), 1)

    def test_new_published_content_is_confirmed(self):
        self.fixture("published.textContent=editor.innerText;editor.remove();")
        state, _ = self.runner.publish(self.page, "reply", self.draft["text"], self.guard, timeout_sec=0.15)
        self.assertEqual(state, "posted")

    def test_new_success_toast_is_confirmed(self):
        self.fixture("toast.textContent='回复成功';")
        state, _ = self.runner.publish(self.page, "reply", self.draft["text"], self.guard, timeout_sec=0.15)
        self.assertEqual(state, "posted")

    def test_editor_preserves_literal_text(self):
        self.fixture()
        self.assertEqual(self.page.locator("#editor").inner_text(), self.draft["text"])

    def test_open_topic_reply_rejects_click_without_detail_navigation(self):
        self.page.set_content('''<ul><li class="dataBody_td" data-uuid="abc">
            <div class="comment" onclick="window.clicked=true; Discuss.openDetail('abc')">回复</div>
            </li></ul><script>window.Discuss={openDetail(){}};window.clicked=false</script>''')
        self.assertFalse(self.runner.open_topic_reply(self.page, "abc", timeout_sec=0.15))
        self.assertTrue(self.page.evaluate("window.clicked"))

    def test_open_topic_reply_confirms_target_detail_url(self):
        def route(request):
            if "replysList" in request.request.url:
                request.fulfill(content_type="text/html; charset=utf-8", body='<div class="topicDetail_detail">目标话题</div>'
                                     '<textarea placeholder="回复话题"></textarea>')
            else:
                request.fulfill(content_type="text/html; charset=utf-8", body='''<li class="dataBody_td" data-uuid="abc">
                    <div class="comment" onclick="window.clicked=true;Discuss.openDetail('abc')">回复</div>
                    </li><script>window.Discuss={openDetail(key){
                    location.href='/course/topic/v3/bbs/b/'+key+'/replysList'
                    }}</script>''')
        self.page.route("https://discussion.test/**", route)
        self.page.goto("https://discussion.test/course/topic/topicList")
        self.assertEqual(self.page.locator(".comment").count(), 1, self.page.content()[:500])
        self.assertEqual(self.page.evaluate("typeof Discuss.openDetail"), "function")
        self.assertIn("openDetail", self.page.locator(".dataBody_td").inner_html())
        self.assertTrue(self.runner.open_topic_reply(self.page, "abc", timeout_sec=1), self.page.url)
        self.assertIn("/abc/replysList", self.page.url)

    def test_open_topic_reply_follows_real_site_popup(self):
        key = "fdb54d8c-4c4b-4cbc-99bf-48dbab286983"
        def route(request):
            if "replysList" in request.request.url:
                request.fulfill(content_type="text/html; charset=utf-8",
                                body='<textarea placeholder="回复话题"></textarea>')
            else:
                request.fulfill(content_type="text/html; charset=utf-8", body=f'''
                    <li class="dataBody_td" data-uuid="{key}">
                    <span class="topicli_title_text">扩大生物安全投入会如何改变未来治理?</span>
                    <div class="comment" onclick="Discuss.openDetail('{key}')">回复</div></li>
                    <script>window.Discuss={{openDetail(k){{window.open('/course/topic/v3/bbs/b/'
                    +k+'/replysList?courseId=123')}}}}</script>''')
        self.page.context.route("https://discussion.test/**", route)
        self.page.goto("https://discussion.test/course/topic/topicList")
        self.runner.list_topics(self.page)

        self.assertTrue(self.runner.open_topic_reply(self.page, key, timeout_sec=1))
        reply_page = self.runner.reply_page(self.page)
        self.assertIsNot(reply_page, self.page)
        self.assertIn(f"/{key}/replysList", reply_page.url)
        self.assertTrue(self.runner.fill_editor(reply_page, "只填目标话题")['body'])
        self.assertEqual(reply_page.locator("textarea").input_value(), "只填目标话题")

    def test_detail_without_reply_editor_is_not_ready(self):
        self.page.route("https://discussion.test/**", lambda route: route.fulfill(
            content_type="text/html; charset=utf-8", body='<div class="topicDetail_detail">加载失败</div>'))
        self.page.goto("https://discussion.test/course/topic/v3/bbs/b/abc/replysList")
        self.assertFalse(self.runner.open_topic_reply(self.page, "abc", timeout_sec=0.15))

    def test_reply_url_in_query_is_not_a_target_detail_page(self):
        self.page.route("https://discussion.test/**", lambda route: route.fulfill(
            content_type="text/html; charset=utf-8", body='<textarea placeholder="回复话题"></textarea>'))
        self.page.goto("https://discussion.test/compose?next=/course/topic/v3/bbs/b/abc/replysList")
        self.assertFalse(self.runner.open_topic_reply(self.page, "abc", timeout_sec=0.15))

    def test_reply_fill_uses_the_verified_reply_input(self):
        self.page.route("https://discussion.test/**", lambda route: route.fulfill(
            content_type="text/html; charset=utf-8", body='''
                <textarea placeholder="回复话题"></textarea>
                <div id="unrelated" contenteditable="true"></div>'''))
        self.page.goto("https://discussion.test/course/topic/v3/bbs/b/abc/replysList")
        self.assertTrue(self.runner.open_topic_reply(self.page, "abc", timeout_sec=0.15))

        self.assertTrue(self.runner.fill_editor(self.page, "目标话题的回复")["body"])

        self.assertEqual(self.page.locator("textarea").input_value(), "目标话题的回复")
        self.assertEqual(self.page.locator("#unrelated").inner_text(), "")

    def test_reply_target_navigation_prevents_filling_another_topic(self):
        self.page.route("https://discussion.test/**", lambda route: route.fulfill(
            content_type="text/html; charset=utf-8", body='<textarea placeholder="回复话题"></textarea>'))
        self.page.goto("https://discussion.test/course/topic/v3/bbs/b/abc/replysList")
        self.assertTrue(self.runner.open_topic_reply(self.page, "abc", timeout_sec=0.15))
        self.page.goto("https://discussion.test/course/topic/v3/bbs/b/other/replysList")

        self.assertIsNone(self.runner.reply_page(self.page))
        self.assertFalse(self.runner.fill_editor(self.page, "不能填到其他话题")["body"])
        self.assertEqual(self.page.locator("textarea").input_value(), "")

    def test_reply_target_navigation_prevents_submitting_another_topic(self):
        self.page.route("https://discussion.test/**", lambda route: route.fulfill(
            content_type="text/html; charset=utf-8", body='''<textarea placeholder="回复话题"></textarea>
                <div class="jb_btn addReply" onclick="window.clicks++">回复</div>
                <script>window.clicks=0</script>'''))
        self.page.goto("https://discussion.test/course/topic/v3/bbs/b/abc/replysList")
        self.assertTrue(self.runner.open_topic_reply(self.page, "abc", timeout_sec=0.15))
        self.assertTrue(self.runner.fill_editor(self.page, "目标回复")["body"])
        self.page.goto("https://discussion.test/course/topic/v3/bbs/b/other/replysList")

        state, _ = self.runner.publish(self.page, "reply", "目标回复", self.guard, timeout_sec=0.15)

        self.assertEqual(state, "draft")
        self.assertEqual(self.page.evaluate("window.clicks"), 0)

    def test_reply_fill_uses_verified_frame_not_unrelated_editor(self):
        key = "a" * 32
        def route(request):
            if "replysList" in request.request.url:
                request.fulfill(content_type="text/html; charset=utf-8",
                                body='''<textarea placeholder="回复话题"></textarea>
                                    <div class="jb_btn addReply" onclick="window.sent=true">回复</div>''')
            else:
                request.fulfill(content_type="text/html; charset=utf-8", body=f'''
                    <li class="dataBody_td"><span class="topicli_title_text">目标话题</span>
                    <div class="comment" onclick="Discuss.openDetail('{key}')">回复</div></li>
                    <script>window.Discuss={{openDetail(k){{location.href='/course/topic/v3/bbs/b/'
                    +k+'/replysList'}}}}</script>''')
        self.page.route("https://discussion.test/**", route)
        self.page.set_content('''<div id="unrelated" contenteditable="true"></div>
            <div class="jb_btn addReply" onclick="window.wrong=true">回复</div>
            <iframe id="board" src="https://discussion.test/course/topic/topicList"></iframe>''')
        self.page.frame_locator("#board").locator(".comment").wait_for()
        self.runner.list_topics(self.page)
        self.assertTrue(self.runner.open_topic_reply(self.page, key, timeout_sec=1))
        self.assertTrue(self.runner.fill_editor(self.page, "只填目标回复")["body"])
        self.assertEqual(self.page.locator("#unrelated").inner_text(), "")
        self.assertEqual(self.page.frame_locator("#board").locator("textarea").input_value(), "只填目标回复")
        self.assertTrue(self.runner.submit_visible(self.page, "reply")[0])
        self.assertFalse(self.page.evaluate("window.wrong||false"))
        self.assertTrue(self.page.frame_locator("#board").locator("body").evaluate("n => n.ownerDocument.defaultView.sent"))

    def test_second_reply_restores_board_before_opening_next_topic(self):
        first, second = "a" * 32, "b" * 32
        def route(request):
            if "replysList" in request.request.url:
                request.fulfill(content_type="text/html; charset=utf-8", body='<div class="topicDetail_detail">'
                                '目标话题</div><textarea placeholder="回复话题"></textarea>')
            else:
                rows = ''.join(f'''<li class="dataBody_td"><span class="topicli_title_text">话题</span>
                    <div class="comment" onclick="Discuss.openDetail('{key}')">回复</div></li>'''
                    for key in (first, second))
                request.fulfill(content_type="text/html; charset=utf-8", body=rows + '''<script>window.Discuss={
                    openDetail(key){location.href='/course/topic/v3/bbs/b/'+key+'/replysList'}
                    }</script>''')
        self.page.route("https://discussion.test/**", route)
        self.page.goto("https://discussion.test/course/topic/topicList")
        self.assertEqual(len(self.runner.list_topics(self.page)), 2)
        self.assertTrue(self.runner.open_topic_reply(self.page, first, timeout_sec=1))
        self.assertTrue(self.runner.open_topic_reply(self.page, second, timeout_sec=1))
        self.assertIn(f"/{second}/replysList", self.page.url)

    def test_auto_batch_publishes_two_replies_on_distinct_topics(self):
        first, second = "a" * 32, "b" * 32
        sent = []
        self.page.on("console", lambda msg: sent.append(msg.text) if msg.text == "reply-sent" else None)
        def route(request):
            if "replysList" in request.request.url:
                request.fulfill(content_type="text/html; charset=utf-8", body='''<textarea placeholder="回复话题"></textarea>
                    <div class="jb_btn jb_btn_92 addReply" onclick="console.log('reply-sent');
                    document.querySelector('#toast').textContent='回复成功'">回复</div>
                    <div id="toast" role="status"></div>''')
            else:
                rows = ''.join(f'''<li class="dataBody_td"><span class="topicli_title_text">话题</span>
                    <div class="comment" onclick="Discuss.openDetail('{key}')">回复</div></li>'''
                    for key in (first, second))
                request.fulfill(content_type="text/html; charset=utf-8", body=rows + '''<script>window.Discuss={
                    openDetail(key){location.href='/course/topic/v3/bbs/b/'+key+'/replysList'}
                    }</script>''')
        self.page.route("https://discussion.test/**", route)
        self.page.goto("https://discussion.test/course/topic/topicList")
        self.assertEqual(len(self.runner.list_topics(self.page)), 2)
        sched = self.scheduler()
        sched._discuss_drafts["reply"] = [dict(self.draft, topic_key=first),
            dict(self.draft, topic_key=second, text="第二条不同内容", fp=content_fp("第二条不同内容"))]
        sched._wait = lambda sec: iter(())
        sched.do_discuss_auto("reply")
        request = next(value for level, value in sched.out.queue if level == "discuss_confirm")
        sched.do_discuss_auto_confirm(request["token"], approved=True)
        list(sched.task)
        self.assertEqual(self.store.discuss_done_counts(self.cid)["replies"], 2,
                         (list(sched.out.queue), self.page.url, sent,
                          self.page.locator("#toast").inner_text(), self.store.list_discussions(self.cid)))
        self.assertEqual(sched._discuss_cursor["reply"], 2)
        self.assertEqual(sent.count("reply-sent"), 2)

    def test_auto_batch_replies_through_two_popup_pages(self):
        first, second = "a" * 32, "b" * 32
        def route(request):
            if "replysList" in request.request.url:
                request.fulfill(content_type="text/html; charset=utf-8", body='''
                    <textarea placeholder="回复话题"></textarea>
                    <div class="jb_btn jb_btn_92 addReply" onclick="
                        const t=document.querySelector('textarea').value.trim();
                        document.querySelector('#toast').textContent=t?'回复成功':'回复失败';
                    ">回复</div><div id="toast" role="status"></div>''')
            else:
                rows = ''.join(f'''<li class="dataBody_td" data-uuid="{key}">
                    <span class="topicli_title_text">话题</span>
                    <div class="comment" onclick="Discuss.openDetail('{key}')">回复</div></li>'''
                    for key in (first, second))
                request.fulfill(content_type="text/html; charset=utf-8", body=rows + '''
                    <script>window.Discuss={openDetail(key){window.open(
                    '/course/topic/v3/bbs/b/'+key+'/replysList?courseId=123')}}</script>''')
        self.page.context.route("https://discussion.test/**", route)
        self.page.goto("https://discussion.test/course/topic/topicList")
        self.runner.list_topics(self.page)
        sched = self.scheduler()
        sched._discuss_drafts["reply"] = [
            dict(self.draft, topic_key=first),
            dict(self.draft, topic_key=second, text="第二条不同内容", fp=content_fp("第二条不同内容")),
        ]
        sched._wait = lambda sec: iter(())

        list(sched._discuss_auto_gen("reply"))

        self.assertEqual(self.store.discuss_done_counts(self.cid)["replies"], 2)
        self.assertEqual(sched._discuss_cursor["reply"], 2)
        self.assertIn("topicList", self.page.url)
        detail_pages = [p for p in self.page.context.pages if "replysList" in p.url]
        self.assertEqual(len(detail_pages), 2)
        self.assertEqual([p.locator("textarea").input_value() for p in detail_pages],
                         [self.draft["text"], "第二条不同内容"])

    def test_ambiguous_button_never_clicks(self):
        self.fixture()
        self.page.evaluate("document.body.appendChild(send.cloneNode(true))")
        state, _ = self.runner.publish(self.page, "reply", self.draft["text"], self.guard, timeout_sec=0.15)
        self.assertEqual(state, "draft")
        self.assertEqual(self.page.evaluate("window.clicks"), 0)

    def test_real_post_div_button_is_clicked(self):
        self.page.set_content('''<input name="title" value="课程提问"><div contenteditable="true">正文</div>
            <div class="edit_btn"><div class="jb_btn fs14 fr jb_btn_92"
                onclick="window.sent=(window.sent||0)+1;document.querySelector('#success').textContent='发布成功'">发布</div></div>
            <div id="success" role="status"></div>''')
        state, _ = self.runner.publish(self.page, "new_post", "正文", self.guard, timeout_sec=0.15)
        self.assertEqual(state, "posted")
        self.assertEqual(self.page.evaluate("window.sent"), 1)

    def test_ueditor_content_update_enables_real_post_button(self):
        self.page.set_content('''<iframe id="ueditor_0" srcdoc="<body contenteditable='true'></body>"></iframe>
            <div class="edit_btn"><div id="send" class="jb_btn_92_disable" onclick="window.sent=1;
                document.querySelector('#success').textContent='发布成功'">发布</div></div>
            <div id="success" role="status"></div>''')
        self.page.frame_locator("#ueditor_0").locator("body").wait_for()
        self.page.evaluate('''() => { window.UE={instants:{ueditorInstant0:{setContent(html) {
            document.querySelector('#ueditor_0').contentDocument.body.innerHTML=html;
            document.querySelector('#send').className='jb_btn_92';
        }}}}; }''')
        result = self.runner.fill_editor(self.page, "A < B", title="课程提问")
        self.assertTrue(result["body"])
        state, _ = self.runner.publish(self.page, "new_post", "A < B", self.guard, timeout_sec=0.15)
        self.assertEqual(state, "posted")
        self.assertEqual(self.page.evaluate("window.sent"), 1)

    def test_real_reply_submit_prefers_add_reply_over_open_reply(self):
        self.page.set_content('''<textarea placeholder="回复话题">正文</textarea>
            <div class="replyBtn" onclick="window.opened=(window.opened||0)+1">回复</div>
            <div class="jb_btn jb_btn_92 addReply" onclick="window.sent=(window.sent||0)+1;
                document.querySelector('#success').textContent='回复成功'">回复</div>
            <div id="success" role="status"></div>''')
        state, _ = self.runner.publish(self.page, "reply", "正文", self.guard, timeout_sec=0.15)
        self.assertEqual(state, "posted")
        self.assertEqual(self.page.evaluate("window.sent"), 1)
        self.assertEqual(self.page.evaluate("window.opened||0"), 0)

    def test_site_replied_flag_survives_empty_local_history(self):
        self.page.set_content('''<ul class="dataBody"><li class="dataBody_td">
            <div class="topicli_head"><span class="topic_reply">已回复</span></div>
            <span class="topicli_title_text">已参与的话题</span>
            <div class="comment" onclick="Discuss.openDetail('08844f439bc744beaeaf4ad6d1171c48')">回复</div></li></ul>''')
        rows = self.runner.list_topics(self.page)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["key"], "08844f439bc744beaeaf4ad6d1171c48")
        self.assertTrue(rows[0]["replied"])

    def test_site_replied_topic_not_replanned_without_local_record(self):
        self.store.upsert_grades(self.cid, [{"name": "讨论", "kind": "discussion", "weight": 10,
                                            "score": 0, "full": 2, "note": "回复+2分，满分2"}])
        self.runner.list_topics = lambda page: [{"key": "topic-1", "title": "已参与的话题", "replied": True}]
        acts, _, _ = self.runner.compute_plan(self.page, {"id": self.cid, "name": "测试课程"}, set())
        self.assertNotIn({"type": "reply", "topic_key": "topic-1"}, acts)

    def test_locally_posted_after_grade_sync_counts_toward_goal(self):
        self.store.upsert_grades(self.cid, [{"name": "讨论", "kind": "discussion", "weight": 10,
                                            "score": 0, "full": 2, "note": "回复+2分，满分2"}])
        self.store.exec("UPDATE grades SET synced_at='2000-01-01 00:00:00' WHERE course_id=?", (self.cid,))
        self.store.mark_discussed(self.cid, "reply", "topic-1", self.draft["fp"])
        self.runner.list_topics = lambda page: [
            {"key": "topic-1", "title": "已参与", "replied": True},
            {"key": "topic-2", "title": "未参与", "replied": False}]
        acts, _, _ = self.runner.compute_plan(self.page, {"id": self.cid, "name": "测试课程"},
                                              self.store.replied_topic_keys(self.cid))
        self.assertEqual(acts, [])

    def test_same_second_post_still_follows_grade_sync(self):
        self.store.upsert_grades(self.cid, [{"name": "讨论", "kind": "discussion", "weight": 10,
                                            "score": 0, "full": 2, "note": "回复+2分，满分2"}])
        self.store.mark_discussed(self.cid, "reply", "topic-1", self.draft["fp"])
        synced = self.store.list_grades(self.cid)[0]["synced_at"]
        posted = self.store.list_discussions(self.cid)[0]["created_at"]
        self.assertGreater(posted, synced)

    def test_stale_grade_refresh_does_not_forget_confirmed_reply(self):
        grade = {"name": "讨论", "kind": "discussion", "weight": 10,
                 "score": 0, "full": 2, "note": "回复+2分，满分2"}
        self.store.upsert_grades(self.cid, [grade])
        self.store.mark_discussed(self.cid, "reply", "topic-1", self.draft["fp"], status="posted")
        self.store.exec("UPDATE discussions SET created_at='2000-01-01 00:00:00' WHERE course_id=?", (self.cid,))
        self.store.upsert_grades(self.cid, [grade])  # 平台成绩延迟，仍返回 0 分
        self.runner.list_topics = lambda page: [{"key": "topic-2", "title": "未参与", "replied": False}]
        acts, _, _ = self.runner.compute_plan(self.page, {"id": self.cid}, set())
        self.assertEqual(acts, [])

    def test_grade_catching_up_does_not_double_count_confirmed_reply(self):
        grade = {"name": "讨论", "kind": "discussion", "weight": 10,
                 "score": 0, "full": 4, "note": "回复+2分，满分4"}
        self.store.upsert_grades(self.cid, [grade])
        self.store.mark_discussed(self.cid, "reply", "topic-1", self.draft["fp"], status="posted")
        self.store.upsert_grades(self.cid, [dict(grade, score=2)])
        self.runner.list_topics = lambda page: [{"key": "topic-2", "title": "未参与", "replied": False}]
        acts, _, _ = self.runner.compute_plan(self.page, {"id": self.cid}, set())
        self.assertEqual(acts, [{"type": "reply", "topic_key": "topic-2"}])

    def test_old_grade_row_keeps_prior_sync_as_credit_baseline(self):
        grade = {"name": "讨论", "kind": "discussion", "weight": 10,
                 "score": 0, "full": 2, "note": "回复+2分，满分2"}
        self.store.upsert_grades(self.cid, [grade])
        self.store.exec("UPDATE grades SET synced_at='2000-01-01 00:00:00',"
                        " credit_base_score=NULL, credit_base_at=NULL WHERE course_id=?", (self.cid,))
        self.store.mark_discussed(self.cid, "reply", "topic-1", self.draft["fp"], status="posted")
        self.store.upsert_grades(self.cid, [grade])
        self.runner.list_topics = lambda page: [{"key": "topic-2", "title": "未参与", "replied": False}]
        acts, _, _ = self.runner.compute_plan(self.page, {"id": self.cid}, set())
        self.assertEqual(acts, [])

    def test_confirmation_after_stale_refresh_uses_actual_post_time(self):
        grade = {"name": "讨论", "kind": "discussion", "weight": 10,
                 "score": 0, "full": 2, "note": "回复+2分，满分2"}
        self.store.upsert_grades(self.cid, [grade])
        self.store.mark_discussed(self.cid, "reply", "topic-1", self.draft["fp"], status="draft")
        self.store.upsert_grades(self.cid, [grade])
        self.store.confirm_discuss_published(self.cid, self.draft["fp"])
        self.runner.list_topics = lambda page: [{"key": "topic-2", "title": "未参与", "replied": False}]
        acts, _, _ = self.runner.compute_plan(self.page, {"id": self.cid}, set())
        self.assertEqual(acts, [])

    def test_posted_at_records_confirmation_not_draft_creation(self):
        self.store.mark_discussed(self.cid, "reply", "topic-1", self.draft["fp"], status="draft")
        self.store.exec("UPDATE discussions SET created_at='2000-01-01 00:00:00' WHERE course_id=?", (self.cid,))
        self.store.confirm_discuss_published(self.cid, self.draft["fp"])
        row = self.store.list_discussions(self.cid)[0]
        self.assertGreater(row["posted_at"], row["created_at"])

    def test_stale_toast_and_preexisting_content_do_not_confirm(self):
        self.fixture()
        self.page.evaluate("toast.textContent='回复成功';published.textContent=editor.innerText")
        state, _ = self.runner.publish(self.page, "reply", self.draft["text"], self.guard, timeout_sec=0.15)
        self.assertEqual(state, "pending_verify")

    def test_visible_failure_overrides_editor_close(self):
        self.fixture("toast.textContent='回复失败';editor.remove();")
        state, _ = self.runner.publish(self.page, "reply", self.draft["text"], self.guard, timeout_sec=0.15)
        self.assertEqual(state, "pending_verify")

    def test_disabled_permission_prevents_click(self):
        self.fixture()
        self.cfg.values["PRACTICE_ALLOW_DISCUSS_SUBMIT"] = False
        state, _ = self.runner.publish(self.page, "reply", self.draft["text"], self.guard, timeout_sec=0.15)
        self.assertEqual(state, "draft")
        self.assertEqual(self.page.evaluate("window.clicks"), 0)

    def test_cancel_prevents_click(self):
        self.fixture()
        stop = threading.Event()
        stop.set()
        state, _ = self.runner.publish(self.page, "reply", self.draft["text"], self.guard,
                                       timeout_sec=0.15, stop_event=stop)
        self.assertEqual(state, "draft")
        self.assertEqual(self.page.evaluate("window.clicks"), 0)

    def test_fill_never_submits_and_manual_confirmation_advances(self):
        self.fixture()
        sched = self.scheduler()
        self.runner.open_topic_reply = lambda page, key: True
        sched.do_discuss_fill_next("reply")
        self.assertEqual(self.page.evaluate("window.clicks"), 0)
        self.assertEqual(sched._discuss_cursor["reply"], 0)
        self.assertEqual(self.store.discuss_done_counts(self.cid)["replies"], 0)
        sched.do_discuss_confirm("reply")
        self.assertEqual(self.store.discuss_done_counts(self.cid)["replies"], 1)
        self.assertEqual(sched._discuss_cursor["reply"], 1)

    def test_half_auto_fills_verified_popup_not_board_editor(self):
        key = "fdb54d8c-4c4b-4cbc-99bf-48dbab286983"
        def route(request):
            if "replysList" in request.request.url:
                request.fulfill(content_type="text/html; charset=utf-8",
                                body='<textarea placeholder="回复话题"></textarea>')
            else:
                request.fulfill(content_type="text/html; charset=utf-8", body=f'''
                    <li class="dataBody_td" data-uuid="{key}">
                    <span class="topicli_title_text">目标话题</span>
                    <div class="comment" onclick="Discuss.openDetail('{key}')">回复</div></li>
                    <script>window.Discuss={{openDetail(k){{window.open('/course/topic/v3/bbs/b/'
                    +k+'/replysList?courseId=123')}}}}</script>''')
        self.page.context.route("https://discussion.test/**", route)
        self.page.set_content('''<div id="unrelated" contenteditable="true"></div>
            <iframe id="board" src="https://discussion.test/course/topic/topicList"></iframe>''')
        self.page.frame_locator("#board").locator(".comment").wait_for()
        self.runner.list_topics(self.page)
        sched = self.scheduler()
        sched._discuss_drafts["reply"] = [dict(self.draft, topic_key=key)]

        sched.do_discuss_fill_next("reply")

        reply_page = self.runner.reply_page(self.page)
        self.assertIsNot(reply_page, self.page)
        self.assertEqual(reply_page.locator("textarea").input_value(), self.draft["text"])
        self.assertEqual(self.page.locator("#unrelated").inner_text(), "")
        self.assertEqual(self.store.list_discussions(self.cid)[0]["status"], "draft")

    def test_half_auto_stops_if_verified_popup_closes_before_fill(self):
        key = "fdb54d8c-4c4b-4cbc-99bf-48dbab286983"
        def route(request):
            if "replysList" in request.request.url:
                request.fulfill(content_type="text/html; charset=utf-8",
                                body='<textarea placeholder="回复话题"></textarea>')
            else:
                request.fulfill(content_type="text/html; charset=utf-8", body=f'''
                    <li class="dataBody_td" data-uuid="{key}">
                    <span class="topicli_title_text">目标话题</span>
                    <div class="comment" onclick="Discuss.openDetail('{key}')">回复</div></li>
                    <script>window.Discuss={{openDetail(k){{window.open('/course/topic/v3/bbs/b/'
                    +k+'/replysList?courseId=123')}}}}</script>''')
        self.page.context.route("https://discussion.test/**", route)
        self.page.set_content('''<div id="unrelated" contenteditable="true"></div>
            <iframe id="board" src="https://discussion.test/course/topic/topicList"></iframe>''')
        self.page.frame_locator("#board").locator(".comment").wait_for()
        self.runner.list_topics(self.page)
        sched = self.scheduler()
        sched._discuss_drafts["reply"] = [dict(self.draft, topic_key=key)]
        real_open = self.runner.open_topic_reply
        def close_after_open(page, topic_key):
            opened = real_open(page, topic_key)
            if opened:
                self.runner.reply_page(page).close()
            return opened
        self.runner.open_topic_reply = close_after_open

        sched.do_discuss_fill_next("reply")

        self.assertEqual(self.page.locator("#unrelated").inner_text(), "")
        self.assertEqual(self.store.list_discussions(self.cid), [])

    def test_half_auto_refuses_editor_on_wrong_topic(self):
        self.page.set_content('<div id="editor" contenteditable="true">旧话题内容</div>')
        sched = self.scheduler()
        sched.do_discuss_fill_next("reply")
        self.assertEqual(self.page.locator("#editor").inner_text(), "旧话题内容")
        self.assertEqual(self.store.list_discussions(self.cid), [])

    def test_half_auto_rejects_already_posted_fingerprint(self):
        self.page.set_content('<div id="editor" contenteditable="true"></div>')
        self.store.mark_discussed(self.cid, "reply", "abc", self.draft["fp"], status="posted")
        sched = self.scheduler()
        self.runner.open_topic_reply = lambda page, key: True
        sched.do_discuss_fill_next("reply")
        self.assertEqual(self.page.locator("#editor").inner_text(), "")
        self.assertEqual(sched._discuss_pending, {})

    def test_auto_skips_stale_reply_to_already_posted_topic(self):
        self.fixture("toast.textContent='回复成功';")
        self.store.mark_discussed(self.cid, "reply", "abc", content_fp("此前的不同回复"), status="posted")
        sched = self.scheduler()
        list(sched._discuss_auto_gen("reply"))
        self.assertEqual(self.page.evaluate("window.clicks"), 0)
        self.assertEqual(sched._discuss_cursor["reply"], 1)
        self.assertEqual(self.store.discuss_done_counts(self.cid)["replies"], 1)

    def test_batch_requires_confirmation_and_cancel_is_read_only(self):
        self.fixture()
        sched = self.scheduler()
        sched.do_discuss_auto("reply")
        self.assertIsNone(sched.task)
        events = list(sched.out.queue)
        request = next(value for level, value in events if level == "discuss_confirm")
        self.assertIn("目标话题", request["text"])
        sched.do_discuss_auto_confirm(request["token"], approved=False)
        self.assertIsNone(sched.task)
        self.assertEqual(self.page.evaluate("window.clicks"), 0)

    def test_unverified_batch_stops_without_advancing_or_second_click(self):
        self.fixture()
        sched = self.scheduler()
        sched._discuss_drafts["reply"].append(dict(self.draft, text="第二条", fp=content_fp("第二条")))
        # 仅模拟页面导航和时间等待；发布点击、核验、数据库记录均使用真实实现。
        self.runner.open_topic_reply = lambda page, key: True
        sched._wait = lambda sec: iter(())
        real_publish = self.runner.publish_gen
        def quick_publish(page, kind, text, guard, **kw):
            return real_publish(page, kind, text, guard, timeout_sec=0.15, **kw)
        with patch.object(self.runner, "publish_gen", side_effect=quick_publish):
            list(sched._discuss_auto_gen("reply"))
        self.assertEqual(self.page.evaluate("window.clicks"), 1)
        self.assertEqual(sched._discuss_cursor["reply"], 0)
        self.assertEqual(self.store.list_discussions(self.cid)[0]["status"], "pending_verify")
        self.assertEqual(self.store.discuss_done_counts(self.cid)["replies"], 0)
        sched.do_discuss_auto("reply")
        self.assertIsNone(sched.task)

    def test_user_can_resolve_confirmed_not_published_and_retry(self):
        self.fixture()
        sched = self.scheduler()
        self.store.mark_discussed(self.cid, "reply", "abc", self.draft["fp"], status="pending_verify")
        sched.do_discuss_not_published("reply")
        self.assertEqual(self.store.list_discussions(self.cid)[0]["status"], "draft")
        self.assertEqual(self.store.discuss_done_counts(self.cid)["replies"], 0)
        sched.do_discuss_auto("reply")
        self.assertTrue(any(level == "discuss_confirm" for level, _ in sched.out.queue))

    def test_auto_recovers_matching_pending_post_from_current_course_board(self):
        post = {"type": "new_post", "topic_key": "", "title": "课程提问",
                "text": "这是已发布的正文。", "fp": content_fp("这是已发布的正文。")}
        self.page.route("https://discussion.test/**", lambda route: route.fulfill(
            content_type="text/html; charset=utf-8", body='''<li class="dataBody_td">
                <span class="topicli_title_text">课程提问</span>
                <div class="topicli_content">这是已发布的正文。</div>
                <div onclick="openDetail('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')"></div></li>'''))
        self.page.goto("https://discussion.test/course/topic/topicList?courseid=123&clazzid=456&cpi=789")
        sched = self.scheduler()
        sched.course["course_key"] = "123_789_456"
        sched._discuss_drafts["new_post"] = [post]
        sched._discuss_pending["new_post"] = {"course_id": self.cid, "index": 0, "draft": dict(post)}
        self.store.mark_discussed(self.cid, "new_post", "", post["fp"], post["title"], status="pending_verify")

        sched.do_discuss_auto("new_post")

        self.assertEqual(self.store.list_discussions(self.cid)[0]["status"], "posted")
        self.assertEqual(sched._discuss_cursor["new_post"], 1)
        self.assertEqual(sched._discuss_pending, {})
        self.assertFalse(any(level == "discuss_confirm" for level, _ in sched.out.queue))

    def test_auto_clears_in_memory_pending_after_record_was_confirmed(self):
        post = {"type": "new_post", "topic_key": "", "title": "课程提问",
                "text": "已经确认的正文", "fp": content_fp("已经确认的正文")}
        sched = self.scheduler()
        sched._discuss_drafts["new_post"] = [post]
        sched._discuss_pending["new_post"] = {"course_id": self.cid, "index": 0, "draft": dict(post)}
        self.store.mark_discussed(self.cid, "new_post", "", post["fp"], post["title"], status="posted")

        sched.do_discuss_auto("new_post")

        self.assertEqual(sched._discuss_pending, {})
        self.assertEqual(sched._discuss_cursor["new_post"], 1)
        self.assertFalse(any(level == "discuss_confirm" for level, _ in sched.out.queue))

    def test_auto_does_not_recover_pending_post_with_wrong_content(self):
        post = {"type": "new_post", "topic_key": "", "title": "课程提问",
                "text": "原本要发布的正文。", "fp": content_fp("原本要发布的正文。")}
        self.page.route("https://discussion.test/**", lambda route: route.fulfill(
            content_type="text/html; charset=utf-8", body='''<li class="dataBody_td">
                <span class="topicli_title_text">课程提问</span>
                <div class="topicli_content">另一条不同的正文。</div>
                <div onclick="openDetail('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')"></div></li>'''))
        self.page.goto("https://discussion.test/course/topic/topicList?courseid=123&clazzid=456&cpi=789")
        sched = self.scheduler()
        sched.course["course_key"] = "123_789_456"
        sched._discuss_drafts["new_post"] = [post]
        self.store.mark_discussed(self.cid, "new_post", "", post["fp"], post["title"], status="pending_verify")

        sched.do_discuss_auto("new_post")

        self.assertEqual(self.store.list_discussions(self.cid)[0]["status"], "pending_verify")
        self.assertEqual(sched._discuss_cursor["new_post"], 0)
        self.assertFalse(any(level == "discuss_confirm" for level, _ in sched.out.queue))

    def test_visible_post_verification_reads_full_body(self):
        body = "长正文" * 200
        self.page.route("https://discussion.test/**", lambda route: route.fulfill(
            content_type="text/html; charset=utf-8", body=f'''<li class="dataBody_td">
                <span class="topicli_title_text">长帖</span>
                <div class="topicli_content">{body}</div>
                <div onclick="openDetail('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')"></div></li>'''))
        self.page.goto("https://discussion.test/course/topic/topicList?courseid=123&clazzid=456&cpi=789")
        self.assertTrue(self.runner.published_post_visible(
            self.page, {"course_key": "123_789_456"}, "长帖", content_fp(body)))

    def test_visible_post_verification_checks_all_loaded_rows(self):
        rows = ''.join(f'''<li class="dataBody_td"><span class="topicli_title_text">其他{i}</span>
            <div class="topicli_content">其他正文</div>
            <div onclick="openDetail('{i:032x}')"></div></li>''' for i in range(200))
        rows += '''<li class="dataBody_td"><span class="topicli_title_text">目标帖</span>
            <div class="topicli_content">目标正文</div>
            <div onclick="openDetail('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')"></div></li>'''
        self.page.route("https://discussion.test/**", lambda route: route.fulfill(
            content_type="text/html; charset=utf-8", body=rows))
        self.page.goto("https://discussion.test/course/topic/topicList?courseid=123&clazzid=456&cpi=789")
        self.assertTrue(self.runner.published_post_visible(
            self.page, {"course_key": "123_789_456"}, "目标帖", content_fp("目标正文")))

    def test_visible_post_verification_rejects_duplicate_loaded_match(self):
        row = '''<li class="dataBody_td"><span class="topicli_title_text">目标帖</span>
            <div class="topicli_content">目标正文</div>
            <div onclick="openDetail('{}')"></div></li>'''
        filler = ''.join(f'''<li class="dataBody_td"><span class="topicli_title_text">其他{i}</span>
            <div class="topicli_content">其他正文</div>
            <div onclick="openDetail('{i:032x}')"></div></li>''' for i in range(199))
        html = row.format('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa') + filler + row.format('bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb')
        self.page.route("https://discussion.test/**", lambda route: route.fulfill(
            content_type="text/html; charset=utf-8", body=html))
        self.page.goto("https://discussion.test/course/topic/topicList?courseid=123&clazzid=456&cpi=789")
        self.assertFalse(self.runner.published_post_visible(
            self.page, {"course_key": "123_789_456"}, "目标帖", content_fp("目标正文")))

    def test_visible_post_verification_rejects_other_course(self):
        self.page.route("https://discussion.test/**", lambda route: route.fulfill(
            content_type="text/html; charset=utf-8", body='''<li class="dataBody_td">
                <span class="topicli_title_text">目标帖</span>
                <div class="topicli_content">目标正文</div>
                <div onclick="openDetail('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')"></div></li>'''))
        self.page.goto("https://discussion.test/course/topic/topicList?courseid=999&clazzid=456&cpi=789")
        self.assertFalse(self.runner.published_post_visible(
            self.page, {"course_key": "123_789_456"}, "目标帖", content_fp("目标正文")))

    def test_auto_continues_when_post_appears_after_uncertain_submit(self):
        post = {"type": "new_post", "topic_key": "", "title": "课程提问",
                "text": "已经在列表中的正文。", "fp": content_fp("已经在列表中的正文。")}
        self.page.route("https://discussion.test/**", lambda route: route.fulfill(
            content_type="text/html; charset=utf-8", body='''<li class="dataBody_td">
                <span class="topicli_title_text">课程提问</span>
                <div class="topicli_content">已经在列表中的正文。</div>
                <div onclick="openDetail('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')"></div></li>'''))
        self.page.set_content('''<input name="title"><div contenteditable="true"></div>
            <iframe id="board" src="https://discussion.test/course/topic/topicList?courseid=123&clazzid=456&cpi=789"></iframe>''')
        self.page.frame_locator("#board").locator(".dataBody_td").wait_for()
        sched = self.scheduler()
        sched.course["course_key"] = "123_789_456"
        sched._discuss_drafts["new_post"] = [post]
        sched._wait = lambda sec: iter(())
        self.runner.open_new_post = lambda page: True

        def uncertain_submit(*args, **kwargs):
            if False:
                yield None
            return "pending_verify", "页面切换时结果未确认"

        with patch.object(self.runner, "publish_gen", side_effect=uncertain_submit):
            list(sched._discuss_auto_gen("new_post"))

        self.assertEqual(self.store.list_discussions(self.cid)[0]["status"], "posted")
        self.assertEqual(sched._discuss_cursor["new_post"], 1)
        self.assertEqual(sched._discuss_pending, {})

    def test_auto_records_the_actual_fallback_title_for_later_verification(self):
        post = {"type": "new_post", "topic_key": "", "title": "",
                "text": "没有单独标题的正文内容", "fp": content_fp("没有单独标题的正文内容")}
        self.page.set_content('<input name="title"><div contenteditable="true"></div>')
        sched = self.scheduler()
        sched._discuss_drafts["new_post"] = [post]
        sched._wait = lambda sec: iter(())
        self.runner.open_new_post = lambda page: True

        def uncertain_submit(*args, **kwargs):
            if False:
                yield None
            return "pending_verify", "结果未确认"

        with patch.object(self.runner, "publish_gen", side_effect=uncertain_submit):
            list(sched._discuss_auto_gen("new_post"))

        self.assertEqual(self.store.list_discussions(self.cid)[0]["title"], "没有单独标题的正文内容")

    def test_manual_confirmation_after_restart_updates_record(self):
        sched = self.scheduler()
        self.store.mark_discussed(self.cid, "reply", "abc", self.draft["fp"], status="pending_verify")
        sched.do_discuss_confirm("reply")
        self.assertEqual(self.store.discuss_done_counts(self.cid)["replies"], 1)

    def test_cancel_preserves_cursor_and_invalidates_confirmation(self):
        self.fixture()
        sched = self.scheduler()
        sched.do_discuss_auto("reply")
        request = next(value for level, value in sched.out.queue if level == "discuss_confirm")
        sched._discuss_cursor["reply"] = 1
        sched.do_cancel()
        self.assertEqual(sched._discuss_cursor["reply"], 1)
        sched.do_discuss_auto_confirm(request["token"], approved=True)
        self.assertIsNone(sched.task)

    def test_revoked_permission_after_batch_confirmation_blocks_start(self):
        self.fixture()
        sched = self.scheduler()
        sched.do_discuss_auto("reply")
        request = next(value for level, value in sched.out.queue if level == "discuss_confirm")
        self.cfg.values["PRACTICE_ALLOW_DISCUSS_SUBMIT"] = False
        sched.do_discuss_auto_confirm(request["token"], approved=True)
        self.assertIsNone(sched.task)
        self.assertEqual(self.page.evaluate("window.clicks"), 0)

    def test_cancel_after_click_preserves_unverified_result(self):
        self.fixture()
        stop = threading.Event()
        gen = self.runner.publish_gen(self.page, "reply", self.draft["text"], self.guard,
                                      timeout_sec=5, stop_event=stop)
        next(gen)
        self.assertEqual(self.page.evaluate("window.clicks"), 1)
        stop.set()
        with self.assertRaises(StopIteration) as done:
            next(gen)
        self.assertEqual(done.exception.value[0], "pending_verify")

    def test_verified_batch_counts_and_advances(self):
        self.fixture("toast.textContent='回复成功';")
        sched = self.scheduler()
        self.runner.open_topic_reply = lambda page, key: True
        sched._wait = lambda sec: iter(())
        sched.do_discuss_auto("reply")
        request = next(value for level, value in sched.out.queue if level == "discuss_confirm")
        sched.do_discuss_auto_confirm(request["token"], approved=True)
        list(sched.task)
        self.assertEqual(self.page.evaluate("window.clicks"), 1)
        self.assertEqual(self.store.discuss_done_counts(self.cid)["replies"], 1)
        self.assertEqual(sched._discuss_cursor["reply"], 1)
        self.assertEqual(sched._discuss_pending, {})

    def test_workbench_confirmation_dialog_and_buttons_construct(self):
        import tkinter as tk
        from ui.workbench import Workbench
        root = tk.Tk()
        root.withdraw()
        wb = Workbench(self.cfg)
        try:
            with patch("tkinter.Tk", return_value=root), patch.object(root, "mainloop"), patch.object(
                    Scheduler, "start"), patch.object(wb, "_load_courses"):
                wb.run()
            root.update_idletasks()
            # 两个讨论卡片各有可操作的确认按钮，确认窗口可展示完整清单并取消。
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            buttons = [w for w in descendants(root) if w.winfo_class() == "TButton"]
            self.assertEqual(sum(str(w.cget("text")) == "确认已发布" for w in buttons), 2)
            wb._confirm_discuss_batch({"token": 1, "text": "本轮 1 条：目标话题与完整草稿"})
            dialog = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
            dialog.withdraw()
            cancel = next(w for w in descendants(dialog) if w.winfo_class() == "TButton" and w.cget("text") == "取消")
            cancel.invoke()
            self.assertEqual(wb.sched.jobs.get_nowait(), ("discuss_auto_confirm", {"token": 1, "approved": False}))
        finally:
            root.destroy()

    def test_manually_opened_post_can_be_filled_without_new_topic_button(self):
        self.fixture()
        self.page.evaluate("document.body.insertAdjacentHTML('afterbegin', '<input name=title>')")
        sched = self.scheduler()
        sched._discuss_drafts["new_post"] = [dict(self.draft, type="new_post", title="课程提问")]
        self.runner.open_new_post = lambda page: False
        sched.do_discuss_fill_next("new_post")
        self.assertEqual(self.page.locator("input[name=title]").input_value(), "课程提问")
        self.assertEqual(self.page.evaluate("window.clicks"), 0)

    def cli(self, fill=False, submit=False, pick=1, topic_open=True):
        import argparse
        import main
        from tests.browser_selftest import FakeBrowser
        self.store.upsert_grades(self.cid, [{"name": "讨论", "kind": "discussion", "weight": 10,
                                            "score": 0, "full": 100}])
        app = main.App(cfg=self.cfg, store=self.store)
        app.browser = FakeBrowser(self.page, self.cfg)
        app.crawler = SimpleNamespace()
        app.pick_course = lambda needle: {"id": self.cid, "name": "测试课程"}
        app.need_ai = lambda: None
        args = argparse.Namespace(course="", fill=fill, submit=submit, pick=pick, kind="reply")
        with patch.object(DiscussionRunner, "open_board", return_value=True), patch.object(
                DiscussionRunner, "prepare_drafts", return_value=([self.draft], "")), patch.object(
                DiscussionRunner, "open_topic_reply", return_value=topic_open):
            return main.cmd_discuss(app, args)

    def test_cli_half_auto_q_stops_before_filling(self):
        self.fixture()
        self.page.locator("#editor").fill("")
        with patch("builtins.input", return_value="q"):
            self.cli(fill=True)
        self.assertEqual(self.page.locator("#editor").inner_text().strip(), "")
        self.assertEqual(self.store.list_discussions(self.cid), [])

    def test_cli_half_auto_fills_real_popup_reply(self):
        import argparse
        import main
        from tests.browser_selftest import FakeBrowser
        key = "fdb54d8c-4c4b-4cbc-99bf-48dbab286983"
        def route(request):
            if "replysList" in request.request.url:
                request.fulfill(content_type="text/html; charset=utf-8",
                                body='<textarea placeholder="回复话题"></textarea>')
            else:
                request.fulfill(content_type="text/html; charset=utf-8", body=f'''
                    <li class="dataBody_td" data-uuid="{key}">
                    <span class="topicli_title_text">目标话题</span>
                    <div class="comment" onclick="Discuss.openDetail('{key}')">回复</div></li>
                    <script>window.Discuss={{openDetail(k){{window.open('/course/topic/v3/bbs/b/'
                    +k+'/replysList?courseId=123')}}}}</script>''')
        self.page.context.route("https://discussion.test/**", route)
        self.page.goto("https://discussion.test/course/topic/topicList")
        self.store.upsert_grades(self.cid, [{"name": "讨论", "kind": "discussion", "weight": 10,
                                            "score": 0, "full": 100}])
        app = main.App(cfg=self.cfg, store=self.store)
        app.browser = FakeBrowser(self.page, self.cfg)
        app.crawler = SimpleNamespace()
        app.pick_course = lambda needle: {"id": self.cid, "name": "测试课程"}
        app.need_ai = lambda: None
        args = argparse.Namespace(course="", fill=True, submit=False, pick=1, kind="reply")
        with patch.object(DiscussionRunner, "open_board", return_value=True), patch.object(
                DiscussionRunner, "prepare_drafts", return_value=([dict(self.draft, topic_key=key)], "")), patch(
                "builtins.input", side_effect=["", "q"]):
            main.cmd_discuss(app, args)

        detail = next(p for p in self.page.context.pages if "replysList" in p.url)
        self.assertEqual(detail.locator("textarea").input_value(), self.draft["text"])
        self.assertEqual(self.store.list_discussions(self.cid)[0]["status"], "draft")

    def test_cli_auto_submits_only_verified_popup_reply(self):
        import argparse
        import main
        from tests.browser_selftest import FakeBrowser
        key = "fdb54d8c-4c4b-4cbc-99bf-48dbab286983"
        def route(request):
            if "replysList" in request.request.url:
                request.fulfill(content_type="text/html; charset=utf-8", body='''
                    <textarea placeholder="回复话题"></textarea>
                    <div class="jb_btn jb_btn_92 addReply" onclick="
                    document.querySelector('#toast').textContent=
                    document.querySelector('textarea').value.trim()?'回复成功':'回复失败'">回复</div>
                    <div id="toast" role="status"></div>''')
            else:
                request.fulfill(content_type="text/html; charset=utf-8", body=f'''
                    <li class="dataBody_td" data-uuid="{key}">
                    <span class="topicli_title_text">目标话题</span>
                    <div class="comment" onclick="Discuss.openDetail('{key}')">回复</div></li>
                    <script>window.Discuss={{openDetail(k){{window.open('/course/topic/v3/bbs/b/'
                    +k+'/replysList?courseId=123')}}}}</script>''')
        self.page.context.route("https://discussion.test/**", route)
        self.page.goto("https://discussion.test/course/topic/topicList")
        self.store.upsert_grades(self.cid, [{"name": "讨论", "kind": "discussion", "weight": 10,
                                            "score": 0, "full": 100}])
        app = main.App(cfg=self.cfg, store=self.store)
        app.browser = FakeBrowser(self.page, self.cfg)
        app.crawler = SimpleNamespace()
        app.pick_course = lambda needle: {"id": self.cid, "name": "测试课程"}
        app.need_ai = lambda: None
        args = argparse.Namespace(course="", fill=False, submit=True, pick=1, kind="reply")
        with patch.object(DiscussionRunner, "open_board", return_value=True), patch.object(
                DiscussionRunner, "prepare_drafts", return_value=([dict(self.draft, topic_key=key)], "")), patch(
                "builtins.input", return_value="y"), patch("sys.stdout", io.StringIO()):
            main.cmd_discuss(app, args)

        detail = next(p for p in self.page.context.pages if "replysList" in p.url)
        self.assertEqual(detail.locator("textarea").input_value(), self.draft["text"])
        self.assertEqual(self.store.discuss_done_counts(self.cid)["replies"], 1)

    def test_cli_half_auto_explicit_success_updates_status(self):
        self.fixture()
        with patch("builtins.input", side_effect=["", "y"]):
            self.cli(fill=True)
        self.assertEqual(self.store.discuss_done_counts(self.cid)["replies"], 1)
        self.assertEqual(self.page.evaluate("window.clicks"), 0)

    def test_cli_half_auto_wrong_topic_does_not_fill(self):
        self.fixture()
        self.page.locator("#editor").fill("")
        with patch("builtins.input", return_value=""):
            self.cli(fill=True, topic_open=False)
        self.assertEqual(self.page.locator("#editor").inner_text().strip(), "")
        self.assertEqual(self.store.list_discussions(self.cid), [])

    def test_cli_half_auto_existing_fingerprint_does_not_fill(self):
        self.fixture()
        self.page.locator("#editor").fill("")
        self.store.mark_discussed(self.cid, "reply", "abc", self.draft["fp"], status="posted")
        with patch("builtins.input", return_value=""):
            self.cli(fill=True)
        self.assertEqual(self.page.locator("#editor").inner_text().strip(), "")

    def test_cli_auto_declined_confirmation_never_publishes(self):
        self.fixture()
        with patch("builtins.input", return_value="n"):
            self.cli(submit=True)
        self.assertEqual(self.store.list_discussions(self.cid), [])
        self.assertEqual(self.page.evaluate("window.clicks"), 0)


if __name__ == "__main__":
    unittest.main()
