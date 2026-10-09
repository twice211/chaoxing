"""Result scope belongs to the executing request, never the polling UI."""
from __future__ import annotations

import queue
import tempfile
import unittest
from pathlib import Path

from config import Config
from ui.web_bridge import BridgeApi, DesktopScheduler


class EventScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = Config()
        self.cfg.values.update(DB_PATH=str(Path(self.temp.name) / "missing.db"), AI_API_KEY="")
        self.scheduler = DesktopScheduler(self.cfg, queue.Queue())
        self.scheduler.course = {"id": 1}
        self.api = BridgeApi(self.cfg, scheduler=self.scheduler)

    def tearDown(self) -> None:
        self.scheduler.shutdown()
        self.api._close()
        self.temp.cleanup()

    def events(self):
        return self.api.poll()["data"]["events"]

    def test_result_and_terminal_share_executing_request_and_course(self) -> None:
        self.scheduler.do_search = lambda text: self.scheduler._emit(("search_results", "course A result"))
        rid = self.api.command("search", {"text": "fixture"})["request_id"]
        self.scheduler._pump_once()
        events = self.events()
        self.assertEqual([event["course_id"] for event in events], [1, 1])
        self.assertEqual([event["request_id"] for event in events], [rid, rid])

    def test_late_generator_result_keeps_original_course_after_selection_changes(self) -> None:
        def task():
            yield None
            self.scheduler._emit(("grades", "course A grades"))
        self.scheduler.do_grades = lambda save=True: self.scheduler._start_task(task())
        rid = self.api.command("grades")["request_id"]
        self.scheduler._pump_once()
        self.scheduler.course = {"id": 2}
        self.scheduler._pump_once()
        grade = next(event for event in self.events() if event["level"] == "grades")
        self.assertEqual(grade["course_id"], 1)
        self.assertEqual(grade["request_id"], rid)

    def test_global_setting_error_does_not_inherit_selected_course(self) -> None:
        self.scheduler.do_save_settings = lambda **kwargs: self.scheduler._emit(("err", "fixture denied"))
        self.api.command("save_settings")
        self.scheduler._pump_once()
        events = self.events()
        self.assertTrue(events)
        self.assertTrue(all(event["course_id"] is None for event in events))

    def test_course_selection_events_are_scoped_to_destination(self) -> None:
        def select(course_id):
            self.scheduler.course = {"id": course_id}
            self.scheduler._emit(("discuss_post", ""))
        self.scheduler.do_select_course = select
        self.api.command("select_course", {"course_id": 2})
        self.scheduler._pump_once()
        events = self.events()
        self.assertTrue(all(event["course_id"] == 2 for event in events))

    def test_unowned_popup_task_captures_course_but_keeps_legacy_tuple_interface(self) -> None:
        def task():
            yield None
            self.scheduler._emit(("ai", "popup A"))
        self.scheduler._start_task(task())
        self.scheduler._step_task()
        self.scheduler.course = {"id": 2}
        self.scheduler._step_task()
        record = self.scheduler.out.get_nowait()
        level, payload = record
        self.assertEqual((level, payload), ("ai", "popup A"))
        self.assertEqual(record.course_id, 1)
        self.assertIsNone(record.request_id)


if __name__ == "__main__":
    unittest.main()
