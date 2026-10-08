# -*- coding: utf-8 -*-
"""
course.study —— 自动学习主循环

流程：登录检查 → 目录同步 → 取“未完成”学习项（支持断点续学）→ 按类型处理
      视频：正常播放到结束；文档/PPT：真实停留并抓取正文入知识库；
      测验/练习：交给刷题辅助（AI 分析，作答与提交由你本人完成）
      网络异常自动重试；重试超过上限则记录失败并跳过，绝不无限循环。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from browser import actions as A
from course.crawler import CourseCrawler
from utils.console import err, info, ok, section, warn
from utils.logger import get_logger
from utils.retry import LoopGuard, LoopGuardTripped
from utils.text import one_line
from utils.time_util import fmt_duration
from video.player import VideoWatcher

log = get_logger("course.study")

VIDEO_KINDS = {"video", "live"}
DOC_KINDS = {"document", "ppt"}
TASK_KINDS = {"exam", "work", "practice"}


@dataclass
class StudyRunner:
    browser: Any
    cfg: Any
    store: Any
    crawler: CourseCrawler
    watcher: VideoWatcher
    kb: Any = None
    practice: Any = None            # questions.practice.PracticeRunner（可选）
    popup: Any = None               # questions.popup.PopupWatcher（可选）

    def run(self, course: dict[str, Any], limit: int = 0, kinds: list[str] | None = None,
            auto_practice: bool = False, build_kb: bool = True, redo_all: bool = False,
            should_stop: Any = None) -> dict[str, int]:
        """自动学习一门课程的未完成内容。"""
        if not self.browser.wait_for_manual_login():
            err("未检测到登录状态，自动学习已停止（登录与验证需由你本人完成）")
            return {}
        items = self.store.list_items(int(course["id"]), only_unfinished=not redo_all,
                                     kinds=kinds or None, limit=2000)
        if not items:
            info("本地没有待处理的学习项，先同步章节目录…")
            from course.models import Course

            c = Course(course_key=course["course_key"], name=course["name"], url=course.get("url", ""),
                       cpi=course.get("cpi", ""), clazzid=course.get("clazzid", ""), id=int(course["id"]))
            self.crawler.sync_catalog(c)
            items = self.store.list_items(int(course["id"]), only_unfinished=not redo_all,
                                          kinds=kinds or None, limit=2000)
        if not items:
            warn("目录同步后仍没有学习项：可能课程把章节放在子页面，或页面结构改版。"
                 "可用 `python main.py dump --url <章节目录页地址>` 导出后在 user_config.py 补选择器。")
            return {}
        cursor = self.store.resume_cursor(int(course["id"]))
        if cursor and not redo_all:
            idx = next((i for i, it in enumerate(items) if int(it["id"]) > cursor), None)
            if idx:
                info(f"检测到上次学习位置（item id={cursor}），从第 {idx + 1} 项继续。")
                items = items[idx:]
        cap = int(limit or self.cfg.get("MAX_ITEMS_PER_RUN") or 100)
        items = items[:cap]
        stat = {"total": len(items), "video": 0, "doc": 0, "task": 0, "done": 0, "fail": 0, "manual": 0}
        section(f"自动学习 ｜ {course['name']} ｜ 待处理 {len(items)} 项")
        print("提示：请保持学习通窗口在前台（切走窗口学习通会暂停播放，本程序不绕过该机制）。\n")
        guard = LoopGuard(max_steps=cap * 3 + 20, stall_limit=15, name="自动学习")
        for pos, item in enumerate(items, 1):
            if should_stop is not None and should_stop():
                warn("已按你的要求停止（进度已保存，下次自动继续）。")
                break
            try:
                guard.step(key=item["id"])
            except LoopGuardTripped as exc:
                warn(str(exc))
                break
            print(f"\n[{pos}/{len(items)}] {item['kind']} ｜ {one_line(item['title'])[:60]}")
            try:
                outcome = self._handle_item(course, item, build_kb=build_kb, auto_practice=auto_practice)
                stat[outcome if outcome in stat else "manual"] = stat.get(outcome, 0) + 1
            except KeyboardInterrupt:
                warn("你按下了 Ctrl+C，已保存进度；下次运行 `study` 会从这里继续。")
                self.store.set_resume_cursor(int(course["id"]), int(item["id"]))
                break
            except Exception as exc:
                log.exception("处理学习项失败")
                err(f"处理失败：{exc}")
                stat["fail"] += 1
                self.store.log_study(int(item["id"]), "fail", str(exc)[:300])
            self.store.set_resume_cursor(int(course["id"]), int(item["id"]))
        self._summary(course, stat)
        return stat

    # ------------------------------------------------------------ 单条处理
    def _handle_item(self, course: dict[str, Any], item: dict[str, Any], build_kb: bool,
                     auto_practice: bool) -> str:
        page = self.browser.start().page
        assert page is not None
        attempts = int(self.cfg.get("MAX_RETRY_PER_ITEM") or 3)
        opened = False
        catalog_url = str(self.store.get_meta(f"catalog_url:{course['id']}", "") or "")
        for attempt in range(1, attempts + 1):
            # fanya://toOld/... 的小节没有真实链接，需要回章节页调用页面自己的跳转函数
            if str(item.get("url") or "").startswith("fanya://"):
                if self.crawler.open_item(page, {**item, "course_id": course["id"]}, catalog_url):
                    opened = True
                    break
            elif A.safe_goto(page, item["url"], attempts=1):
                opened = True
                break
            self.store.bump_attempts(int(item["id"]), error=f"打开失败(第{attempt}次)")
            self.store.log_study(int(item["id"]), "retry", f"第 {attempt} 次打开失败", None)
            time.sleep(min(12.0, 2.0 * attempt))
        if not opened:
            self.store.log_study(int(item["id"]), "fail", "多次重试仍无法打开页面")
            err(f"页面无法打开（已重试 {attempts} 次），跳过：{item['url'][:100]}")
            return "fail"
        A.close_popups(page)
        detail = self.crawler.inspect_section(page)
        kind = item["kind"]
        if kind == "other" and detail.get("kinds"):
            kind = next((k for k in ("video", "document", "ppt") if k in detail["kinds"]), kind)
        if kind != item.get("kind"):
            # 把真实类型回写本地库，status 里就能看到 视频/文档 的分类统计
            self.store.exec("UPDATE items SET kind=? WHERE id=?", (kind, int(item["id"])))
            item["kind"] = kind
            info(f"识别为：{kind}")
        if build_kb and self.kb is not None:
            try:
                self.kb.ingest_page(page, course_id=int(course["id"]),
                                    chapter_key=item.get("chapter_key", ""), title=item.get("title", ""))
            except Exception as exc:
                log.debug("页面正文入库失败：%s", exc)
        if kind in VIDEO_KINDS:
            return self._do_video(course, item, page)
        if kind in DOC_KINDS:
            return self._do_doc(course, item, page, build_kb)
        if kind in TASK_KINDS:
            return self._do_task(course, item, page, auto_practice, detail)
        warn("该类型暂不支持自动学习（按人工处理计数）。")
        self.store.log_study(int(item["id"]), "skip", f"未支持类型 kind={kind}")
        return "manual"

    def _do_video(self, course: dict[str, Any], item: dict[str, Any], page: Any) -> str:
        if self.popup is not None:
            self.popup.reset()
            self.popup.course_id = int(course["id"])
            self.popup.chapter_key = str(item.get("chapter_key") or "")
        res = self.watcher.watch(page, item=item, on_tick=None)
        if res.complete:
            self.store.update_progress(int(item["id"]), 1.0, res.watched_sec)
            self.store.finish_item(int(item["id"]), f"视频播放完成（观看 {fmt_duration(res.watched_sec)}）")
            ok(f"视频学习完成（真实观看 {fmt_duration(res.watched_sec)}）→ 自动进入下一节")
            self.store.log_study(int(item["id"]), "finish", f"观看 {fmt_duration(res.watched_sec)}", res.watched_sec)
            return "video"
        if res.reason == "no_video":
            warn("页面里没有找到播放器，改按文档方式处理。")
            return self._do_doc(course, item, page, build_kb=False)
        err(f"视频未完成（原因：{res.reason}）。进度已保存，稍后可自动继续。")
        self.store.log_study(int(item["id"]), "progress", f"未完成 reason={res.reason}", res.watched_sec)
        return "manual"

    def _do_doc(self, course: dict[str, Any], item: dict[str, Any], page: Any, build_kb: bool) -> str:
        if self.kb is not None and build_kb:
            try:
                self.kb.ingest_page(page, course_id=int(course["id"]),
                                    chapter_key=item.get("chapter_key", ""), title=item.get("title", ""))
            except Exception as exc:
                log.debug("文档入库失败：%s", exc)
        res = self.watcher.read_document(page, item=item)
        if res.complete:
            self.store.update_progress(int(item["id"]), 1.0, res.watched_sec)
            self.store.finish_item(int(item["id"]), f"文档/课件停留 {fmt_duration(res.watched_sec)}")
            ok(f"文档/课件任务完成（停留 {fmt_duration(res.watched_sec)}）")
            return "doc"
        warn("文档任务需你人工确认（程序不伪造完成状态）")
        return "manual"

    def _do_task(self, course: dict[str, Any], item: dict[str, Any], page: Any,
                 auto_practice: bool, detail: dict[str, Any]) -> str:
        info("该条目是章节测验/作业/练习：题目解析与 AI 辅助可用 `practice` 命令。")
        if not auto_practice:
            self.store.log_study(int(item["id"]), "skip", "测验/练习：未开启 --practice，仅登记")
            return "task"
        if self.practice is None:
            warn("刷题模块未初始化")
            return "task"
        try:
            self.practice.run(course, chapter_key=item.get("chapter_key", ""), page=page)
        except Exception as exc:
            log.exception("刷题失败")
            err(f"刷题流程中断：{exc}")
            return "fail"
        return "task"

    # ------------------------------------------------------------ 汇总
    def _summary(self, course: dict[str, Any], stat: dict[str, int]) -> None:
        section("本轮学习小结")
        for key, label in (("video", "视频完成"), ("doc", "文档/课件完成"), ("task", "测验/练习处理"),
                           ("manual", "待人工确认"), ("fail", "失败")):
            info(f"{label}：{stat.get(key, 0)}")
        stats = self.store.stats(int(course["id"]))
        done = int(stats.get("finished") or 0)
        total = int(stats.get("total") or 0)
        pct = (done / total * 100) if total else 0.0
        info(f"课程累计：{done}/{total} 项已完成（{pct:.1f}%）｜ 累计真实学习时长 "
             f"{fmt_duration(float(stats.get('watch_sec') or 0))}")
        info(f"题库 {stats.get('questions')} 题 ｜ 错题 {stats.get('wrong')} 条 ｜ 知识库 "
             f"{stats.get('kb_docs')} 份资料 / {stats.get('kb_chunks')} 个知识块")
        print("\n[重要] 章节测验与考试的“提交答案”必须由你本人点击，本程序不会代提交。")