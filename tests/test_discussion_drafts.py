from __future__ import annotations

import unittest
import queue
from types import SimpleNamespace

from course.discussion import DiscussionRunner
from tests.selftest import temp_store
from ui.workbench import Scheduler


class DiscussionDraftTests(unittest.TestCase):
    def test_switching_course_clears_displayed_drafts_with_cached_drafts(self):
        with temp_store("switch_course_drafts.db") as (store, _):
            first_id = store.upsert_course({"course_key": "first", "name": "课程一"})
            second_id = store.upsert_course({"course_key": "second", "name": "课程二"})
            sched = Scheduler(None, queue.Queue())
            sched.app = SimpleNamespace(store=store)
            sched.course = {"id": first_id, "name": "课程一"}
            sched._discuss_drafts["reply"] = [{"text": "课程一的回复草稿"}]

            sched.do_select_course(second_id)

            self.assertEqual(sched._discuss_drafts["reply"], [])
            events = list(sched.out.queue)
            self.assertIn(("discuss_post", ""), events)
            self.assertIn(("discuss_reply", ""), events)

    def test_auto_names_the_module_that_has_drafts(self):
        sched = Scheduler(None, queue.Queue())
        sched._discuss_drafts["reply"] = [{"text": "已有回复草稿"}]

        sched.do_discuss_auto("new_post")

        message = sched.out.get_nowait()[1]
        self.assertIn("回复模块已有 1 条", message)

    def test_auto_warns_about_unverified_submission_before_missing_drafts(self):
        with temp_store("pending_before_drafts.db") as (store, _):
            cid = store.upsert_course({"course_key": "course", "name": "测试课程"})
            store.mark_discussed(cid, "reply", "topic-1", "pending-fp", status="pending_verify")
            sched = Scheduler(None, queue.Queue())
            sched.app = SimpleNamespace(store=store)
            sched.course = {"id": cid, "name": "测试课程"}

            sched.do_discuss_auto("reply")

            message = sched.out.get_nowait()[1]
            self.assertIn("待核验", message)

    def test_batch_error_falls_back_to_single_draft(self):
        class Engine:
            ai = SimpleNamespace(enabled=True)

            def discussion_batch_text(self, *args, **kwargs):
                raise RuntimeError("batch service unavailable")

            def discussion_text(self, *args, **kwargs):
                return "这是一条单独生成的回复。"

        runner = DiscussionRunner(None, {"DISCUSS_AI_PARALLEL": 1}, None, None, Engine())
        acts = [{"type": "reply", "topic_key": "topic-1"}]
        ctx = {"topic_by_key": {"topic-1": {"title": "课程问题", "snippet": "背景"}}}

        drafts = runner.generate_drafts(acts, ctx, {"name": "测试课程"})

        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["text"], "这是一条单独生成的回复。")
        self.assertTrue(any("batch service unavailable" in error for error in runner.last_errors))


if __name__ == "__main__":
    unittest.main()
