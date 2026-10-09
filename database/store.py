# -*- coding: utf-8 -*-
"""
database.store —— SQLite 持久化封装

职责：
1. 结构建立与迁移（CREATE TABLE IF NOT EXISTS，幂等）；
2. 断点续学：items.done / progress / watch_sec 实时落盘，重启后可继续；
3. 题库与错题库：以“题目指纹 fp”唯一约束实现重复题检测；
4. 知识库：文档 + 分块 + 关键词；
5. 审计：AI 调用、搜索、考试模式行为留痕。

线程说明：Playwright 在独立线程操作页面，本模块通过“每线程一个连接”支持并发。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from utils.logger import get_logger
from utils.text import fingerprint, normalize_for_match
from utils.time_util import now_str

log = get_logger("database.store")

_SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def _hash(text: str, size: int = 16) -> str:
    return hashlib.sha1(str(text).encode("utf-8", "ignore")).hexdigest()[:size]


@dataclass
class Store:
    """轻量 ORM：只写本项目需要的查询，不追求通用性。"""

    db_path: Path
    _local: threading.local = threading.local()
    _write_lock: threading.Lock = threading.Lock()

    def __post_init__(self) -> None:
        self.db_path = Path(self.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._init_schema()

    # ---------------- 基础 ----------------
    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=20, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        sql = _SCHEMA_FILE.read_text(encoding="utf-8")
        with self._write_lock:
            self.conn.executescript(sql)
            self._migrate()
            self.conn.commit()
        log.debug("数据库结构就绪：%s", self.db_path)

    def _migrate(self) -> None:
        """幂等补列：给已存在的旧表补上新增字段(不会重复报错)。"""
        for table, column, decl in (("grades", "note", "TEXT"),
                                    ("grades", "credit_base_score", "REAL"),
                                    ("grades", "credit_base_at", "TEXT"),
                                    ("discussions", "posted_at", "TEXT")):
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if cols and column not in cols:
                try:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                    log.info("迁移：%s 表补列 %s", table, column)
                except sqlite3.OperationalError:
                    # 两个进程同时启动时，另一进程可能刚好完成同一补列。
                    current = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                    if column not in current:
                        raise

    def q(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params).fetchall())

    def q1(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def exec(self, sql: str, params: Sequence[Any] = ()) -> int:
        """写操作统一入口：串行化 + 自动提交，避免多线程写冲突。"""
        with self._write_lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return int(cur.lastrowid or cur.rowcount)

    def exec_many(self, sql: str, rows: Iterable[Sequence[Any]]) -> int:
        data = [tuple(r) for r in rows]
        if not data:
            return 0
        with self._write_lock:
            cur = self.conn.executemany(sql, data)
            self.conn.commit()
            return cur.rowcount

    @staticmethod
    def rows_to_dicts(rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
        return [dict(r) for r in rows]

    # ---------------- meta（断点续学的游标） ----------------
    def get_meta(self, key: str, default: str = "") -> str:
        row = self.q1("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else default

    def set_meta(self, key: str, value: Any) -> None:
        self.exec(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    # ---------------- 课程 ----------------
    def upsert_course(self, course: dict[str, Any]) -> int:
        key = course.get("course_key") or f"c{course.get('course_id','')}"
        now = now_str()
        row = self.q1("SELECT id FROM courses WHERE course_key=?", (key,))
        if row:
            self.exec(
                """UPDATE courses SET name=?, url=?, cpi=?, clazzid=?, teacher=?, class_name=?,
                   progress_text=?, updated_at=? WHERE id=?""",
                (
                    course.get("name", ""), course.get("url", ""), course.get("cpi", ""),
                    course.get("clazzid", ""), course.get("teacher", ""), course.get("class_name", ""),
                    course.get("progress_text", ""), now, row["id"],
                ),
            )
            return int(row["id"])
        return self.exec(
            """INSERT INTO courses(course_key,name,url,cpi,clazzid,teacher,class_name,progress_text,
                                   synced_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                key, course.get("name", ""), course.get("url", ""), course.get("cpi", ""),
                course.get("clazzid", ""), course.get("teacher", ""), course.get("class_name", ""),
                course.get("progress_text", ""), now, now,
            ),
        )

    def list_courses(self) -> list[dict[str, Any]]:
        return self.rows_to_dicts(self.q("SELECT * FROM courses ORDER BY updated_at DESC"))

    def clear_all_courses(self) -> int:
        """退出登录时“谁登录就是谁的”：清空课程及其 章节/小节进度/成绩/讨论/错题/学习流水 与当前课选择。
        保留题库(questions)/知识库(kb)/审计等跨课程数据;学习通服务器上的进度不受影响,重新读取会重建。"""
        n = int(self.q1("SELECT COUNT(*) AS c FROM courses")["c"]) if self.q1("SELECT COUNT(*) AS c FROM courses") else 0
        for tbl in ("chapters", "items", "grades", "discussions", "study_log", "wrong_book"):
            try:
                self.exec(f"DELETE FROM {tbl}")
            except Exception as exc:
                log.debug("清空 %s 失败：%s", tbl, exc)
        self.exec("DELETE FROM courses")
        self.exec("DELETE FROM meta WHERE key='current_course'")
        log.info("退出登录：已清空 %s 门课及其章节/进度/成绩/错题/讨论缓存", n)
        return n

    def get_course(self, needle: str = "") -> dict[str, Any] | None:
        """按 id / 课程名模糊 / course_key 找一门课；needle 为空则返回第一个有未完成项的课。"""
        if needle:
            if str(needle).isdigit():
                row = self.q1("SELECT * FROM courses WHERE id=?", (int(needle),))
                if row:
                    return dict(row)
            row = self.q1(
                "SELECT * FROM courses WHERE name LIKE ? OR course_key=? ORDER BY updated_at DESC LIMIT 1",
                (f"%{needle}%", str(needle)),
            )
            return dict(row) if row else None
        row = self.q1(
            """SELECT c.* FROM courses c JOIN items i ON i.course_id=c.id
               WHERE i.done=0 GROUP BY c.id ORDER BY COUNT(*) DESC LIMIT 1"""
        )
        return dict(row) if row else (self.list_courses()[:1] or [None])[0]

    # ---------------- 章节 ----------------
    def replace_chapters(self, course_id: int, chapters: Sequence[dict[str, Any]]) -> int:
        rows = [
            (
                course_id, ch.get("chap_key") or _hash(ch.get("title", "") + str(i)),
                ch.get("no", ""), ch.get("title", ""), ch.get("parent_key", ""),
                int(ch.get("level", 1) or 1), i,
            )
            for i, ch in enumerate(chapters)
        ]
        with self._write_lock, self.conn as conn:
            conn.execute("DELETE FROM chapters WHERE course_id=?", (course_id,))
            conn.executemany(
                "INSERT OR REPLACE INTO chapters(course_id,chap_key,no,title,parent_key,level,order_idx)"
                " VALUES(?,?,?,?,?,?,?)", rows,
            )
        return len(rows)

    def list_chapters(self, course_id: int) -> list[dict[str, Any]]:
        return self.rows_to_dicts(
            self.q("SELECT * FROM chapters WHERE course_id=? ORDER BY order_idx", (course_id,))
        )

    def find_chapter(self, course_id: int, needle: str) -> dict[str, Any] | None:
        if not needle:
            return None
        row = self.q1(
            "SELECT * FROM chapters WHERE course_id=? AND (chap_key=? OR no=? OR title LIKE ?) LIMIT 1",
            (course_id, needle, needle, f"%{needle}%"),
        )
        return dict(row) if row else None

    # ---------------- 学习项 ----------------
    def upsert_item(self, course_id: int, item: dict[str, Any]) -> dict[str, Any]:
        """写入/更新一个学习项，返回带 id/done 的字典。已完成的项不会被重置。"""
        url = item.get("url") or item.get("item_key") or item.get("title", "")
        key = item.get("item_key") or _hash(normalize_for_match(url) or str(url), 20)
        now = now_str()
        row = self.q1("SELECT * FROM items WHERE course_id=? AND item_key=?", (course_id, key))
        if row:
            self.exec(
                "UPDATE items SET title=?, kind=?, url=?, chapter_key=?, last_seen=? WHERE id=?",
                (item.get("title") or row["title"], item.get("kind") or row["kind"], url,
                 item.get("chapter_key") or row["chapter_key"], now, row["id"]),
            )
            return dict(row)
        new_id = self.exec(
            """INSERT INTO items(course_id,item_key,title,kind,url,chapter_key,done,progress,
                                 attempts,first_seen,last_seen)
               VALUES(?,?,?,?,?,?,0,0,0,?,?)""",
            (course_id, key, item.get("title", ""), item.get("kind", "other"), url,
             item.get("chapter_key", ""), now, now),
        )
        return dict(self.q1("SELECT * FROM items WHERE id=?", (new_id,)))  # type: ignore[arg-type]

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        row = self.q1("SELECT * FROM items WHERE id=?", (item_id,))
        return dict(row) if row else None

    def list_items(
        self, course_id: int, only_unfinished: bool = True, kinds: Sequence[str] | None = None,
        chapter_key: str = "", limit: int = 500,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM items WHERE course_id=?"
        params: list[Any] = [course_id]
        if only_unfinished:
            sql += " AND done=0"
        if kinds:
            sql += " AND kind IN (" + ",".join("?" * len(kinds)) + ")"
            params += list(kinds)
        if chapter_key:
            sql += " AND chapter_key=?"
            params.append(chapter_key)
        sql += " ORDER BY id LIMIT ?"
        params.append(int(limit))
        return self.rows_to_dicts(self.q(sql, params))

    def update_progress(self, item_id: int, progress: float, watch_sec: float | None = None) -> None:
        progress = min(1.0, max(0.0, float(progress)))
        if watch_sec is None:
            self.exec("UPDATE items SET progress=? WHERE id=?", (progress, item_id))
        else:
            self.exec("UPDATE items SET progress=?, watch_sec=? WHERE id=?", (progress, float(watch_sec), item_id))

    def finish_item(self, item_id: int, note: str = "") -> None:
        self.exec(
            "UPDATE items SET done=1, progress=1.0, finished_at=? WHERE id=?",
            (now_str(), item_id),
        )
        self.log_study(item_id, "finish", note)

    def bump_attempts(self, item_id: int, error: str = "") -> int:
        self.exec("UPDATE items SET attempts=attempts+1, last_seen=?, last_error=? WHERE id=?",
                  (now_str(), error[:400], item_id))
        row = self.q1("SELECT attempts FROM items WHERE id=?", (item_id,))
        return int(row["attempts"]) if row else 0

    def log_study(self, item_id: int | None, event: str, detail: str = "", seconds: float | None = None,
                  course_id: int | None = None) -> None:
        if course_id is None and item_id:
            row = self.q1("SELECT course_id FROM items WHERE id=?", (item_id,))
            course_id = int(row["course_id"]) if row else None
        self.exec(
            "INSERT INTO study_log(course_id,item_id,ts,event,detail,seconds) VALUES(?,?,?,?,?,?)",
            (course_id, item_id, now_str(), event, detail[:800], seconds),
        )

    # ---------------- 统计 / 断点续学 ----------------
    def stats(self, course_id: int | None = None) -> dict[str, Any]:
        where, params = ("WHERE course_id=?", (course_id,)) if course_id else ("", ())
        row = self.q1(
            f"""SELECT COUNT(*) total,
                       COALESCE(SUM(CASE WHEN done=1 THEN 1 ELSE 0 END),0) finished,
                       COALESCE(SUM(CASE WHEN done=0 THEN 1 ELSE 0 END),0) remaining,
                       COALESCE(SUM(watch_sec),0) watch_sec,
                       COALESCE(AVG(progress),0) avg_progress
                FROM items {where}""",
            params,
        )
        d = dict(row) if row else {}  # type: ignore[arg-type]
        q_row = self.q1(f"SELECT COUNT(*) n FROM questions {'' if not course_id else 'WHERE course_id=?'}",
                        params if course_id else ())
        w_row = self.q1("SELECT COUNT(*) n, COALESCE(SUM(wrong_count),0) s FROM wrong_book"
                        + (" WHERE course_id=?" if course_id else ""), params if course_id else ())
        kb_row = self.q1("SELECT COUNT(*) docs FROM kb_docs WHERE status='done'"
                         + (" AND course_id=?" if course_id else ""), params if course_id else ())
        kc_row = self.q1("SELECT COUNT(*) chunks FROM kb_chunks"
                         + (" WHERE course_id=?" if course_id else ""), params if course_id else ())
        d.update(
            questions=int(q_row["n"]) if q_row else 0,
            wrong=int(w_row["n"]) if w_row else 0,
            wrong_total=int(w_row["s"]) if w_row else 0,
            kb_docs=int(kb_row["docs"]) if kb_row else 0,
            kb_chunks=int(kc_row["chunks"]) if kc_row else 0,
        )
        return d

    def resume_cursor(self, course_id: int) -> int:
        """返回“上次学到哪”的 item id（0 表示从头开始）。"""
        return int(self.get_meta(f"resume:{course_id}", "0") or 0)

    def set_resume_cursor(self, course_id: int, item_id: int) -> None:
        self.set_meta(f"resume:{course_id}", item_id)

    # ---------------- 成绩明细（只读抓取的缓存；每次整批替换） ----------------
    def upsert_grades(self, course_id: int, rows: list[dict[str, Any]], overview: str = "") -> int:
        """整批替换某课程成绩(先删后插),避免历史叠加。返回写入条数。"""
        now = now_str("%Y-%m-%d %H:%M:%S.%f")
        old = {(r["kind"], r["name"]): r for r in self.list_grades(course_id)}
        data = []
        for r in rows or []:
            if not (r.get("name") or r.get("kind")):
                continue
            name, kind = r.get("name", ""), r.get("kind", "other")
            previous = old.get((kind, name))
            base_score = previous["credit_base_score"] if previous else None
            base_at = previous["credit_base_at"] if previous else None
            if base_score is None and previous and previous["score"] is not None:
                # 旧库首次迁移：沿用旧成绩的同步点，保住此前本地已确认的发布。
                base_score, base_at = previous["score"], previous["synced_at"] or now
            elif base_score is None and r.get("score") is not None:
                base_score, base_at = r["score"], now
            data.append((course_id, name, kind, r.get("score"), r.get("full"), r.get("weight"), overview,
                         r.get("note", ""), now, base_score, base_at))
        with self._write_lock, self.conn as conn:
            conn.execute("DELETE FROM grades WHERE course_id=?", (course_id,))
            conn.executemany(
                "INSERT INTO grades(course_id,name,kind,score,full,weight,overview,note,synced_at,"
                "credit_base_score,credit_base_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", data,
            )
        return len(data)

    def list_grades(self, course_id: int) -> list[dict[str, Any]]:
        return self.rows_to_dicts(
            self.q("SELECT name,kind,score,full,weight,overview,note,synced_at,credit_base_score,"
                   "credit_base_at FROM grades"
                   " WHERE course_id=? ORDER BY id", (course_id,))
        )

    def get_grade_overview(self, course_id: int) -> str:
        row = self.q1("SELECT overview FROM grades WHERE course_id=? LIMIT 1", (course_id,))
        return (row["overview"] or "") if row else ""

    # ---------------- 讨论参与(发帖/回复；内容指纹去重) ----------------
    def is_discussed(self, course_id: int, fp: str) -> bool:
        if not fp:
            return False
        return self.q1("SELECT 1 FROM discussions WHERE course_id=? AND fp=?", (course_id, fp)) is not None

    def _discussion_event_time(self, course_id: int) -> str:
        """生成严格晚于成绩基线的时间戳，兼容同一时钟刻度内同步和发布。"""
        stamp = now_str("%Y-%m-%d %H:%M:%S.%f")
        row = self.q1("SELECT MAX(synced_at) AS synced_at, MAX(credit_base_at) AS base_at"
                      " FROM grades WHERE course_id=?", (course_id,))
        lower = max(str(row["synced_at"] or ""), str(row["base_at"] or "")) if row else ""
        if lower and stamp <= lower:
            from datetime import datetime, timedelta
            stamp = (datetime.fromisoformat(lower) + timedelta(microseconds=1)).strftime(
                "%Y-%m-%d %H:%M:%S.%f")
        return stamp

    def mark_discussed(self, course_id: int, kind: str, topic_key: str, fp: str,
                       title: str = "", url: str = "", status: str = "posted") -> bool:
        """记录一条讨论参与；内容指纹已存在则不重复写(返回 False)。"""
        if not fp or self.is_discussed(course_id, fp):
            return False
        created_at = self._discussion_event_time(course_id)
        self.exec(
            "INSERT INTO discussions(course_id,kind,topic_key,fp,title,status,url,created_at,posted_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (course_id, kind, topic_key, fp, title[:120], status, url, created_at,
             created_at if status == "posted" else None),
        )
        return True

    def update_discuss_status(self, course_id: int, fp: str, status: str) -> None:
        """把某条讨论记录从 draft 升为 posted(自动提交成功后回写,计入“够分即停”)。"""
        if not fp:
            return
        if status == "posted":
            posted_at = self._discussion_event_time(course_id)
            self.exec("UPDATE discussions SET status=?, posted_at=COALESCE(posted_at, ?)"
                      " WHERE course_id=? AND fp=?", (status, posted_at, course_id, fp))
        else:
            self.exec("UPDATE discussions SET status=? WHERE course_id=? AND fp=?",
                      (status, course_id, fp))

    def confirm_discuss_published(self, course_id: int, fp: str) -> bool:
        """在用户确认或页面核验成功后，把一条草稿升级为已发布。

        返回 False 表示该草稿不存在，避免把错误的确认计入讨论完成数。
        """
        if not fp:
            return False
        row = self.q1("SELECT 1 FROM discussions WHERE course_id=? AND fp=?", (course_id, fp))
        if row is None:
            return False
        self.update_discuss_status(course_id, fp, "posted")
        return True

    def discuss_done_counts(self, course_id: int) -> dict[str, int]:
        """已“posted”的发帖/回复条数,供“够目标即停”判定。"""
        rows = self.q("SELECT kind, COUNT(*) n FROM discussions"
                      " WHERE course_id=? AND status='posted' GROUP BY kind", (course_id,))
        d = {"new_posts": 0, "replies": 0}
        for r in rows:
            if r["kind"] == "new_post":
                d["new_posts"] = int(r["n"])
            elif r["kind"] == "reply":
                d["replies"] = int(r["n"])
        return d

    def replied_topic_keys(self, course_id: int) -> set[str]:
        rows = self.q("SELECT DISTINCT topic_key FROM discussions"
                      " WHERE course_id=? AND kind='reply' AND status='posted'", (course_id,))
        return {r["topic_key"] for r in rows if r["topic_key"]}

    def list_discussions(self, course_id: int, limit: int = 100) -> list[dict[str, Any]]:
        return self.rows_to_dicts(
            self.q("SELECT kind,topic_key,fp,title,status,url,created_at,posted_at FROM discussions"
                   " WHERE course_id=? ORDER BY id DESC LIMIT ?", (course_id, int(limit)))
        )

    # ---------------- 题库 ----------------
    def upsert_question(self, question: dict[str, Any], course_id: int | None = None) -> tuple[int, bool]:
        """写入题目；返回 (question_id, 是否重复题)。fp 唯一约束实现重复检测。"""
        stem = question.get("stem", "")
        options = question.get("options") or []
        fp = question.get("fp") or fingerprint(stem, options)
        now = now_str()
        row = self.q1("SELECT * FROM questions WHERE fp=?", (fp,))
        if row:
            self.exec("UPDATE questions SET seen_count=seen_count+1, updated_at=?, section_url=?, kind=? "
                      "WHERE id=?", (now, question.get("section_url") or row["section_url"],
                                     question.get("kind") or row["kind"], row["id"]))
            return int(row["id"]), True
        qid = self.exec(
            """INSERT INTO questions(course_id,fp,kind,stem,options_json,answer,answer_source,analysis,
                                     knowledge,chapter_key,section_url,seen_count,wrong_count,correct_count,
                                     difficulty,is_ai_similar,origin_qid,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,1,0,0,?,?,?,?,?)""",
            (
                course_id, fp, question.get("kind", "unknown"), stem,
                json.dumps(options, ensure_ascii=False), question.get("answer", ""),
                question.get("answer_source", ""), question.get("analysis", ""),
                question.get("knowledge", ""), question.get("chapter_key", ""),
                question.get("section_url", ""), question.get("difficulty", ""),
                1 if question.get("is_ai_similar") else 0, question.get("origin_qid"),
                now, now,
            ),
        )
        return qid, False

    def get_question(self, qid: int) -> dict[str, Any] | None:
        row = self.q1("SELECT * FROM questions WHERE id=?", (qid,))
        if not row:
            return None
        d = dict(row)
        d["options"] = json.loads(d.get("options_json") or "[]")
        return d

    def find_question(self, needle: str, course_id: int | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM questions WHERE stem LIKE ?"
        params: list[Any] = [f"%{needle.strip()}%"]
        if course_id:
            sql += " AND (course_id=? OR course_id IS NULL)"
            params.append(course_id)
        sql += " ORDER BY updated_at DESC LIMIT 1"
        row = self.q1(sql, params)
        if not row:
            return None
        d = dict(row)
        d["options"] = json.loads(d.get("options_json") or "[]")
        return d

    def exists_similar(self, stem: str, options: Sequence[str], threshold: float = 0.92,
                       course_id: int | None = None) -> dict[str, Any] | None:
        """近似重复题检测（同义改写/顺序不同的题）。"""
        from utils.text import similarity

        sql = "SELECT * FROM questions"
        params: list[Any] = []
        if course_id:
            sql += " WHERE course_id=?"
            params.append(course_id)
        sql += " ORDER BY updated_at DESC LIMIT 2000"
        best, best_score = None, 0.0
        for row in self.q(sql, params):
            opts = json.loads(row["options_json"] or "[]")
            score = similarity(stem, row["stem"]) * (0.9 if opts and options and not _same_options(opts, options) else 1.0)
            if score > best_score:
                best, best_score = row, score
        if best and best_score >= threshold:
            d = dict(best)
            d["options"] = json.loads(d.get("options_json") or "[]")
            d["_similarity"] = round(best_score, 3)
            return d
        return None

    def set_question_ai_result(self, qid: int, answer: str = "", analysis: str = "", knowledge: str = "",
                               difficulty: str = "", answer_source: str = "ai") -> None:
        self.exec(
            """UPDATE questions SET answer=COALESCE(NULLIF(?,''),answer),
                                          analysis=COALESCE(NULLIF(?,''),analysis),
                                          knowledge=COALESCE(NULLIF(?,''),knowledge),
                                          difficulty=COALESCE(NULLIF(?,''),difficulty),
                                          answer_source=?, updated_at=? WHERE id=?""",
            (answer, analysis, knowledge, difficulty, answer_source, now_str(), qid),
        )

    def list_questions(self, course_id: int | None = None, limit: int = 200,
                       only_wrong: bool = False, kind: str = "") -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM questions WHERE 1=1", []
        if course_id:
            sql += " AND course_id=?"
            params.append(course_id)
        if only_wrong:
            sql += " AND wrong_count>0"
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        sql += " ORDER BY wrong_count DESC, updated_at DESC LIMIT ?"
        params.append(int(limit))
        out = []
        for row in self.q(sql, params):
            d = dict(row)
            d["options"] = json.loads(d.get("options_json") or "[]")
            out.append(d)
        return out

    # ---------------- 错题本 ----------------
    def mark_wrong(self, qid: int, reason: str = "", note: str = "", course_id: int | None = None,
                   chapter_key: str = "") -> int:
        """答错一次 → wrong_count+1；同步 questions.wrong_count。"""
        row = self.q1("SELECT * FROM wrong_book WHERE question_id=?", (qid,))
        if row:
            self.exec("UPDATE wrong_book SET wrong_count=wrong_count+1, last_wrong_at=?, reason=?, note=?, "
                      "resolved=0 WHERE question_id=?", (now_str(), reason or row["reason"], note or row["note"], qid))
            cnt = int(row["wrong_count"]) + 1
        else:
            self.exec("INSERT INTO wrong_book(question_id,course_id,chapter_key,wrong_count,last_wrong_at,"
                      "reason,note,resolved) VALUES(?,?,?,?,?,?,?,0)",
                      (qid, course_id, chapter_key, 1, now_str(), reason, note))
            cnt = 1
        self.exec("UPDATE questions SET wrong_count=? WHERE id=?", (cnt, qid))
        return cnt

    def mark_correct(self, qid: int) -> None:
        self.exec("UPDATE questions SET correct_count=correct_count+1 WHERE id=?", (qid,))
        row = self.q1("SELECT wrong_count FROM wrong_book WHERE question_id=?", (qid,))
        if row and int(row["wrong_count"]) >= 2:
            self.exec("UPDATE wrong_book SET resolved=1 WHERE question_id=?", (qid,))

    def list_wrong(self, course_id: int | None = None, only_unresolved: bool = True,
                   limit: int = 500) -> list[dict[str, Any]]:
        sql = ("SELECT w.*, q.stem, q.kind, q.options_json, q.answer, q.analysis, q.knowledge, q.chapter_key "
               "FROM wrong_book w JOIN questions q ON q.id=w.question_id WHERE 1=1")
        params: list[Any] = []
        if course_id:
            sql += " AND (w.course_id=? OR q.course_id=?)"
            params += [course_id, course_id]
        if only_unresolved:
            sql += " AND w.resolved=0"
        sql += " ORDER BY w.wrong_count DESC, w.last_wrong_at DESC LIMIT ?"
        params.append(int(limit))
        out = []
        for row in self.q(sql, params):
            d = dict(row)
            d["options"] = json.loads(d.get("options_json") or "[]")
            out.append(d)
        return out

    def wrong_stats(self) -> list[dict[str, Any]]:
        return self.rows_to_dicts(self.q(
            "SELECT COALESCE(knowledge,'(未标注)') knowledge, COUNT(*) n, SUM(wrong_count) times "
            "FROM questions JOIN wrong_book USING(id) GROUP BY knowledge ORDER BY times DESC LIMIT 30"))

    # ---------------- 会话与作答 ----------------
    def start_session(self, mode: str, course_id: int | None = None, title: str = "", url: str = "") -> int:
        return self.exec("INSERT INTO sessions(course_id,mode,title,url,started_at) VALUES(?,?,?,?,?)",
                         (course_id, mode, title, url, now_str()))

    def end_session(self, sid: int, total: int = 0, ai_called: int = 0, note: str = "") -> None:
        self.exec("UPDATE sessions SET ended_at=?, total=?, ai_called=?, note=? WHERE id=?",
                  (now_str(), total, ai_called, note, sid))

    def add_answer(self, session_id: int, qid: int, qno: str, user_answer: str, ai_answer: str,
                   confidence: str, is_correct: int | None = None) -> int:
        return self.exec(
            """INSERT INTO answers(session_id,question_id,qno,user_answer,ai_answer,confidence,is_correct,
                                   submitted_by,updated_at)
               VALUES(?,?,?,?,?,?,?, 'user', ?)""",
            (session_id, qid, qno, user_answer, ai_answer, confidence, is_correct, now_str()),
        )

    # ---------------- 知识库 ----------------
    def kb_add_doc(self, doc: dict[str, Any]) -> int:
        row = self.q1("SELECT id FROM kb_docs WHERE course_id IS ? AND path IS ?",
                      (doc.get("course_id"), doc.get("path")))
        if row:
            self.exec("UPDATE kb_docs SET title=?, doc_type=?, size=?, chars=?, status=?, error=?, "
                      "chapter_key=?, ingested_at=? WHERE id=?",
                      (doc.get("title", ""), doc.get("doc_type", ""), doc.get("size"), doc.get("chars"),
                       doc.get("status", "done"), doc.get("error", ""), doc.get("chapter_key", ""),
                       now_str(), row["id"]))
            self.exec("DELETE FROM kb_chunks WHERE doc_id=?", (row["id"],))
            return int(row["id"])
        return self.exec(
            """INSERT INTO kb_docs(course_id,chapter_key,title,doc_type,path,url,size,chars,status,error,ingested_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (doc.get("course_id"), doc.get("chapter_key", ""), doc.get("title", ""), doc.get("doc_type", ""),
             doc.get("path", ""), doc.get("url", ""), doc.get("size"), doc.get("chars"),
             doc.get("status", "pending"), doc.get("error", ""), now_str()),
        )

    def kb_add_chunks(self, doc_id: int, chunks: Sequence[dict[str, Any]], course_id: int | None = None,
                      chapter_key: str = "") -> int:
        rows = [
            (doc_id, course_id, chunk.get("chapter_key") or chapter_key, int(chunk.get("seq", i)),
             chunk.get("loc", ""), chunk.get("kind", "text"), chunk.get("text", ""),
             " ".join(chunk.get("terms") or []))
            for i, chunk in enumerate(chunks)
        ]
        self.exec_many(
            "INSERT OR REPLACE INTO kb_chunks(doc_id,course_id,chapter_key,seq,loc,kind,text,terms)"
            " VALUES(?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    def kb_all_chunks(self, course_id: int | None = None, kinds: Sequence[str] | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT k.id, k.doc_id, k.course_id, k.chapter_key, k.seq, k.loc, k.kind, k.text, k.terms, "
               "d.title, d.path, d.doc_type FROM kb_chunks k JOIN kb_docs d ON d.id=k.doc_id WHERE 1=1")
        params: list[Any] = []
        if course_id:
            sql += " AND k.course_id=?"
            params.append(course_id)
        if kinds:
            sql += " AND k.kind IN (" + ",".join("?" * len(kinds)) + ")"
            params += list(kinds)
        sql += " ORDER BY k.id"
        return self.rows_to_dicts(self.q(sql, params))

    def kb_docs(self, status: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM kb_docs" + (" WHERE status=?" if status else "") + " ORDER BY ingested_at DESC"
        return self.rows_to_dicts(self.q(sql, (status,) if status else ()))

    def kb_delete_doc(self, doc_id: int) -> None:
        self.exec("DELETE FROM kb_docs WHERE id=?", (doc_id,))

    def kb_search_like(self, needle: str, limit: int = 30) -> list[dict[str, Any]]:
        return self.rows_to_dicts(self.q(
            "SELECT k.*, d.title, d.path FROM kb_chunks k JOIN kb_docs d ON d.id=k.doc_id "
            "WHERE k.text LIKE ? OR k.terms LIKE ? LIMIT ?", (f"%{needle}%", f"%{needle}%", limit)))

    # ---------------- 审计/台账 ----------------
    def log_ai_call(self, purpose: str, model: str, status: str, ms: float, est_chars: int, error: str = "") -> None:
        self.exec("INSERT INTO ai_calls(ts,purpose,model,status,ms,est_chars,error) VALUES(?,?,?,?,?,?,?)",
                  (now_str(), purpose, model, status, ms, est_chars, error[:500]))

    def log_search(self, scope: str, query: str, hits: int) -> None:
        self.exec("INSERT INTO search_log(ts,scope,query,hits) VALUES(?,?,?,?)", (now_str(), scope, query, hits))

    def log_exam_event(self, event: str, detail: str = "", session_id: int | None = None) -> None:
        self.exec("INSERT INTO exam_audit(session_id,ts,event,detail) VALUES(?,?,?,?)",
                  (session_id, now_str(), event, detail[:800]))

    def audit_last(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.rows_to_dicts(self.q(
            "SELECT * FROM exam_audit ORDER BY id DESC LIMIT ?", (limit,)))

    def close(self) -> None:
        """关闭当前线程的数据库连接（Windows 下删除临时库文件前必须调用）。"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None


def _same_options(a: Sequence[str], b: Sequence[str]) -> bool:
    """比较两组选项是否等价：先去掉 “A. / （A）” 前缀，再忽略顺序与标点。"""
    from utils.text import strip_option_prefix

    na = sorted(normalize_for_match(strip_option_prefix(x)) for x in a if x)
    nb = sorted(normalize_for_match(strip_option_prefix(x)) for x in b if x)
    return na == nb
