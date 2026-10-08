# -*- coding: utf-8 -*-
"""
questions.wrongbook —— 错题库：记录、复习、生成强化题、导出

错题来源：
1) 平台判分页只读导入（最准确）；
2) 练习时人工标记“这题我错了”；
3) AI 判分（练习场景）。
每条错题保留：题目/选项/答案/解析/知识点/章节/错误次数，并可一键生成同知识点强化题。
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from questions.models import Question, answers_equal
from ui import report
from utils.console import ask, confirm, err, info, ok, section, warn
from utils.logger import get_logger
from utils.retry import LoopGuard, LoopGuardTripped
from utils.text import one_line
from utils.time_util import now_str

log = get_logger("questions.wrongbook")


@dataclass
class WrongBook:
    store: Any
    engine: Any = None          # ai.responder.AnswerEngine
    cfg: Any = None

    # ------------------------------------------------------------ 记录
    def record(self, qid: int, reason: str = "", note: str = "", course_id: int | None = None,
               chapter_key: str = "") -> int:
        cnt = self.store.mark_wrong(qid, reason=reason, note=note, course_id=course_id,
                                    chapter_key=chapter_key)
        log.info("错题入库：question_id=%s 累计错误 %s 次", qid, cnt)
        return cnt

    def import_grading(self, rows: Sequence[dict[str, Any]], course_id: int | None = None,
                       chapter_key: str = "") -> dict[str, int]:
        """从判分结果页（只读抓取）导入：错的一律进错题库，对的累计答对次数。"""
        stat = {"total": 0, "wrong": 0, "right": 0, "unknown": 0}
        for row in rows:
            stat["total"] += 1
            q = Question(stem=row.get("stem", ""), kind=row.get("kind", "unknown"),
                         options=[{"label": "", "text": one_line(o), "checked": False, "value": ""}
                                  for o in (row.get("options") or [])],
                         ref_answer=row.get("ref_answer", ""), my_answer=row.get("user_answer", ""),
                         chapter_key=chapter_key)
            qid, _dup = self.store.upsert_question(q.to_row(), course_id=course_id)
            if row.get("ref_answer"):
                self.store.set_question_ai_result(qid, answer=row["ref_answer"], answer_source="platform")
            if row.get("is_right") is True:
                stat["right"] += 1
                self.store.mark_correct(qid)
            elif row.get("is_right") is False:
                stat["wrong"] += 1
                self.record(qid, reason="平台判分为错误", course_id=course_id, chapter_key=chapter_key)
            else:
                stat["unknown"] += 1
        info(f"判分导入完成：共 {stat['total']} 题，错 {stat['wrong']}，对 {stat['right']}，"
             f"未识别 {stat['unknown']}")
        return stat

    # ------------------------------------------------------------ 查询
    def list(self, course_id: int | None = None, only_unresolved: bool = True,
             limit: int = 200) -> list[dict[str, Any]]:
        return self.store.list_wrong(course_id=course_id, only_unresolved=only_unresolved, limit=limit)

    def stats_text(self) -> str:
        rows = self.store.wrong_stats()
        if not rows:
            return "错题库为空。"
        lines = ["按知识点统计："]
        for r in rows:
            lines.append(f"  · {r['knowledge']}：{r['n']} 题，累计错 {r['times']} 次")
        return "\n".join(lines)

    # ------------------------------------------------------------ 导出
    def export(self, fmt: str = "md", course_id: int | None = None) -> Path:
        export_dir = Path(self.cfg.path("EXPORT_DIR")) if self.cfg else Path("data/exports")
        export_dir.mkdir(parents=True, exist_ok=True)
        rows = self.list(course_id=course_id, only_unresolved=False, limit=10000)
        stamp = now_str("%Y%m%d_%H%M%S")
        if fmt == "json":
            target = export_dir / f"wrongbook_{stamp}.json"
            target.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        elif fmt == "csv":
            target = export_dir / f"wrongbook_{stamp}.csv"
            with target.open("w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh)
                writer.writerow(["题号", "题型", "题干", "选项", "答案", "解析", "知识点", "错误次数"])
                for i, r in enumerate(rows, 1):
                    writer.writerow([i, r.get("kind"), r.get("stem"), " | ".join(r.get("options") or []),
                                     r.get("answer"), (r.get("analysis") or "")[:500],
                                     r.get("knowledge"), r.get("wrong_count")])
        else:
            target = export_dir / f"wrongbook_{stamp}.md"
            lines = ["# 错题库\n"]
            for i, r in enumerate(rows, 1):
                lines.append(f"## {i}. {r.get('kind', '')} ｜ 错 {r.get('wrong_count', 1)} 次")
                lines.append(r.get("stem", ""))
                for opt in r.get("options") or []:
                    lines.append(f"- {opt}")
                lines.append(f"**答案**：{r.get('answer') or '（待补充）'}  ")
                lines.append(f"**解析**：{r.get('analysis') or '（待补充）'}  ")
                lines.append(f"**知识点**：{r.get('knowledge') or '（待标注）'}\n")
            target.write_text("\n".join(lines), encoding="utf-8")
        ok(f"错题库已导出：{target}（{len(rows)} 题）")
        return target

    # ------------------------------------------------------------ 生成强化题
    def gen_similar(self, question: dict[str, Any], n: int = 2) -> list[int]:
        if not self.engine or not getattr(self.engine, "ai", None) or not self.engine.ai.enabled:
            warn("AI 未启用，无法生成强化题（可先在 user_config.py 配置 AI_API_KEY）")
            return []
        items = self.engine.make_similar(question, n=n)
        ids: list[int] = []
        for it in items:
            q = Question(stem=it["stem"], kind=it["kind"],
                         options=[{"label": chr(65 + i), "text": one_line(o), "checked": False, "value": ""}
                                  for i, o in enumerate(it.get("options") or [])],
                         ref_answer=it.get("answer", ""), analysis=it.get("analysis", ""),
                         knowledge=it.get("knowledge", ""), chapter_key=it.get("chapter_key", ""))
            row = q.to_row()
            row["is_ai_similar"] = 1
            row["origin_qid"] = question.get("id")
            qid, dup = self.store.upsert_question(row, course_id=question.get("course_id"))
            if dup:
                info("生成的强化题与题库重复，已跳过")
                continue
            self.store.set_question_ai_result(qid, answer=it.get("answer", ""), analysis=it.get("analysis", ""),
                                              knowledge=it.get("knowledge", ""), answer_source="ai")
            ids.append(qid)
        ok(f"已生成 {len(ids)} 道同知识点强化题")
        return ids

    # ------------------------------------------------------------ 错题重练
    def practice_loop(self, course_id: int | None = None, limit: int = 10, kb: Any = None) -> dict[str, int]:
        rows = self.list(course_id=course_id)
        if not rows:
            info("错题库暂无待复习题目。")
            return {}
        section(f"错题重练（待复习 {len(rows)} 题）")
        stat = {"done": 0, "right": 0, "wrong": 0, "skip": 0}
        guard = LoopGuard(max_steps=limit * 3, stall_limit=limit * 2 + 5, name="错题重练")
        pool = list(reversed(rows[:limit]))
        while pool:
            try:
                guard.step(key=pool[-1].get("id"))
            except LoopGuardTripped as exc:
                warn(str(exc))
                break
            row = pool.pop()
            q = Question(stem=row.get("stem", ""), kind=row.get("kind", "unknown"),
                         options=[{"label": chr(65 + i), "text": one_line(o), "checked": False, "value": ""}
                                  for i, o in enumerate(row.get("options") or [])],
                         ref_answer=row.get("answer", ""), knowledge=row.get("knowledge", ""))
            stat["done"] += 1
            print("\n" + report.render_question(q, stat["done"], min(limit, len(rows) + stat["done"])))
            if row.get("analysis"):
                info("上次解析：" + row["analysis"][:200])
            ans = ask("你的答案（直接回车=跳过，w=我还是不会，q=结束）").strip()
            if ans.lower() == "q":
                break
            if not ans:
                stat["skip"] += 1
                continue
            correct = None
            if row.get("answer"):
                correct = answers_equal(q.kind, ans, row["answer"])
            if correct is None and self.engine and self.engine.ai.enabled:
                correct, why = self.engine.grade(q.as_dict(), ans, row.get("answer", ""))
                if correct is None:
                    warn(f"该题需要人工判断：{why}")
            if ans.lower() == "w":
                correct = False
            if correct:
                stat["right"] += 1
                self.store.mark_correct(int(row["id"]))
                ok(f"回答正确（参考答案：{row.get('answer') or '—'}）")
            else:
                stat["wrong"] += 1
                self.record(int(row["id"]), reason="重练再次出错", course_id=course_id)
                err(f"仍需加强（参考答案：{row.get('answer') or '待补充'}）")
                if confirm("是否生成 2 道同知识点强化题？", default_no=False):
                    pool.extend(
                        {**row, "id": x} for x in self.gen_similar({**row, "course_id": course_id}, n=2)
                    )
                    pool = [p for p in pool if p.get("id") != row.get("id")]
        ok(f"错题重练结束：答对 {stat['right']}，答错 {stat['wrong']}，跳过 {stat['skip']}")
        return stat