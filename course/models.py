# -*- coding: utf-8 -*-
"""course.models —— 课程/章节/学习项 数据结构（与数据库字段一一对应）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List

from course.selectors import KIND_LABELS


@dataclass
class Course:
    course_key: str
    name: str
    url: str = ""
    cpi: str = ""
    clazzid: str = ""
    teacher: str = ""
    class_name: str = ""
    progress_text: str = ""
    id: int | None = None

    @staticmethod
    def make_key(course_id: str, cpi: str = "", clazzid: str = "") -> str:
        parts = [str(course_id or "").strip()]
        if cpi:
            parts.append(str(cpi).strip())
        if clazzid:
            parts.append(str(clazzid).strip())
        return "_".join(p for p in parts if p) or "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "course_key": self.course_key, "name": self.name, "url": self.url, "cpi": self.cpi,
            "clazzid": self.clazzid, "teacher": self.teacher, "class_name": self.class_name,
            "progress_text": self.progress_text,
        }


@dataclass
class Chapter:
    chap_key: str
    title: str
    no: str = ""
    parent_key: str = ""
    level: int = 1
    order_idx: int = 0

    @property
    def label(self) -> str:
        return f"{self.no} {self.title}".strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "chap_key": self.chap_key, "title": self.title, "no": self.no,
            "parent_key": self.parent_key, "level": self.level, "order_idx": self.order_idx,
        }


@dataclass
class StudyItem:
    item_key: str
    title: str
    kind: str = "other"          # video|document|ppt|exam|work|practice|other
    url: str = ""
    chapter_key: str = ""
    done: bool = False
    platform_done: bool | None = None   # 平台标注的完成状态（None=读不到）
    order_idx: int = 0
    id: int | None = None

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_key": self.item_key, "title": self.title, "kind": self.kind, "url": self.url,
            "chapter_key": self.chapter_key, "done": int(self.done), "order_idx": self.order_idx,
        }


@dataclass
class Catalog:
    course: Course
    chapters: List[Chapter] = field(default_factory=list)
    items: List[StudyItem] = field(default_factory=list)

    def chapter_label(self, chap_key: str) -> str:
        for ch in self.chapters:
            if ch.chap_key == chap_key:
                return ch.label
        return "(未分章)"