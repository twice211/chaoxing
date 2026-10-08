# -*- coding: utf-8 -*-
"""
questions.autofill —— 把“答案文本”映射为 ExamSnapshot.apply_practice_actions 的写操作列表。

仅用于练习/演示模式：真实考试域名由 ExamGuard 在写路径上拦截；本模块不关心页面身份，
只做“给定题目 + 答案 → 该点哪个框 / 填什么内容”的纯逻辑，可离线测试。
"""

from __future__ import annotations

import re
from typing import Any

from questions.models import Question, letters_from, normalize_judge


def _id_sel(raw_id: str) -> str:
    """用属性选择器定位，规避 id 里可能出现的特殊字符。"""
    return f"[id='{raw_id}']"


def _select(option: dict[str, Any]) -> list[dict[str, Any]]:
    """定位选项：优先专用 click_sel→ id → name+序号 → 文本。

    幂等:若该选项**当前已勾选**则返回空——绝不重复点击(重复点会取消已保存的多选勾选)。
    """
    if option.get("checked"):
        return []
    cs = str(option.get("click_sel") or "")
    if cs:
        return [{"selector": cs, "op": "check"}]
    oid = str(option.get("id") or "")
    if oid:
        return [{"selector": _id_sel(oid), "op": "check"}]
    nm = str(option.get("name") or "")
    idx = option.get("nidx")
    if nm and isinstance(idx, int) and idx >= 0:
        return [{"selector": f'input[name="{nm}"] >> nth={idx}', "op": "check"}]
    txt = re.sub(r"\s+", " ", str(option.get("text") or "")).strip()
    if len(txt) >= 1:
        safe = txt.replace('"', "").replace("'", "").replace("\\", "")[:24]
        return [{"selector": f'label:has-text("{safe}")', "op": "check"}]
    return []


def plan_answer_actions(q: Question, answer: str) -> list[dict[str, Any]]:
    """返回可交给 apply_practice_actions 的操作列表；无法定位到元素时返回 []（绝不盲填）。"""
    ans = str(answer or "").strip()
    if not ans:
        return []
    kind = q.kind
    actions: list[dict[str, Any]] = []

    if kind in ("single", "multi"):
        want = set(letters_from(ans))
        for o in q.options:
            if str(o.get("label", "")).upper() in want:
                actions += _select(o)
    elif kind == "judge":
        want_true = normalize_judge(ans) == "正确"
        chosen: dict[str, Any] | None = None
        for o in q.options:
            jt = normalize_judge(str(o.get("text", "")))
            if jt in ("正确", "错误") and (jt == "正确") == want_true:
                chosen = o
                break
        if chosen is None and q.options:  # 回退：正确→首个，错误→次个
            chosen = q.options[0] if want_true else (q.options[1] if len(q.options) > 1 else q.options[0])
        if chosen is not None:
            actions += _select(chosen)
    elif kind in ("blank", "short"):
        parts = [p.strip() for p in re.split(r"\s*/\s*|\n", ans)] if kind == "blank" else [ans]
        blanks = (q.raw or {}).get("blanks") or []
        for i, b in enumerate(blanks):
            bid = str((b or {}).get("id") or "")
            if not bid:
                continue
            if str((b or {}).get("value") or "").strip():   # 已填过则不覆盖(幂等)
                continue
            actions.append({"selector": _id_sel(bid), "op": "fill",
                            "value": parts[i] if i < len(parts) else ""})
    return actions


def plan_is_noop(actions: list[dict[str, Any]]) -> bool:
    """规划结果为空 = 目标已达成(选项都已勾/空都已填),用于“已作答”判定与自检。"""
    return not actions
