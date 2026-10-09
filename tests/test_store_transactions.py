from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from database.store import Store


class StoreReplacementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "fixture.db")
        self.cid = self.store.upsert_course({"course_key": "fixture", "name": "fixture"})
        self.store.replace_chapters(self.cid, [{"chap_key": "original", "title": "original", "level": 1}])
        self.store.upsert_grades(self.cid, [{"kind": "discussion", "name": "discussion", "score": 80}])

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_chapter_validation_error_preserves_original(self) -> None:
        with self.assertRaises(ValueError):
            self.store.replace_chapters(self.cid, [{"chap_key": "new", "title": "new", "level": "invalid"}])
        self.assertEqual([row["chap_key"] for row in self.store.list_chapters(self.cid)], ["original"])

    def test_chapter_insert_failure_rolls_back_delete_and_partial_insert(self) -> None:
        self.store.exec("""CREATE TRIGGER fail_chapter_insert BEFORE INSERT ON chapters
            WHEN NEW.title='fail' BEGIN SELECT RAISE(ABORT, 'fixture failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.replace_chapters(self.cid, [
                {"chap_key": "first", "title": "first", "level": 1},
                {"chap_key": "second", "title": "fail", "level": 1},
            ])
        self.assertEqual([row["chap_key"] for row in self.store.list_chapters(self.cid)], ["original"])
        self.assertFalse(self.store.conn.in_transaction)

    def test_grade_insert_failure_preserves_original_score_and_baseline(self) -> None:
        before = self.store.list_grades(self.cid)
        self.store.exec("""CREATE TRIGGER fail_grade_insert BEFORE INSERT ON grades
            WHEN NEW.name='fail' BEGIN SELECT RAISE(ABORT, 'fixture failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.upsert_grades(self.cid, [
                {"kind": "discussion", "name": "discussion", "score": 90},
                {"kind": "other", "name": "fail", "score": 1},
            ])
        self.assertEqual(self.store.list_grades(self.cid), before)
        self.assertFalse(self.store.conn.in_transaction)

    def test_successful_replacements_and_explicit_empty_replacement(self) -> None:
        self.assertEqual(self.store.replace_chapters(self.cid, [{"chap_key": "new", "title": "new"}]), 1)
        self.assertEqual([row["chap_key"] for row in self.store.list_chapters(self.cid)], ["new"])
        baseline = self.store.list_grades(self.cid)[0]["credit_base_score"]
        self.assertEqual(self.store.upsert_grades(self.cid, [{"kind": "discussion", "name": "discussion", "score": 90}]), 1)
        self.assertEqual(self.store.list_grades(self.cid)[0]["score"], 90)
        self.assertEqual(self.store.list_grades(self.cid)[0]["credit_base_score"], baseline)
        self.assertEqual(self.store.replace_chapters(self.cid, []), 0)
        self.assertEqual(self.store.upsert_grades(self.cid, []), 0)
        self.assertEqual(self.store.list_chapters(self.cid), [])
        self.assertEqual(self.store.list_grades(self.cid), [])


if __name__ == "__main__":
    unittest.main()
