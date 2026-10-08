# -*- coding: utf-8 -*-
"""
course.grades —— 成绩明细的“纯解析”(不含浏览器,便于离线自检)

输入是从页面抓来的“原始行”列表,每行至少含 name 与一段文本(text);
这里只负责把文本里的 得分/满分/占比 抠出来并归类,任何解析不出的一律留 None(绝不臆造分数)。
"""

from __future__ import annotations

import re
from typing import Any

# 讨论类关键词:功能3 用它挑“计分的讨论任务”
DISCUSSION_KEYS: tuple[str, ...] = ("讨论", "话题", "回复", "发帖")

# 名称 → 粗分类(顺序即优先级,先命中先返回)
_KIND_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("discussion", DISCUSSION_KEYS),
    ("exam", ("考试", "期末", "期中", "测验", "test", "exam")),
    ("work", ("作业", "assignment", "任务")),
    ("practice", ("练习", "自测", "题库")),
    ("video", ("视频", "观看", "章节学习")),
    ("doc", ("文档", "阅读", "课件", "资料")),
]


def is_discussion_name(text: str) -> bool:
    t = str(text or "")
    return any(k in t for k in DISCUSSION_KEYS)


def _kind_of(name: str) -> str:
    low = str(name or "").lower()
    for kind, keys in _KIND_HINTS:
        if any(k.lower() in low for k in keys):
            return kind
    return "other"


def _nums(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"[0-9]+(?:\.[0-9]+)?", str(text or ""))]


def parse_grade_rows(raw: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in raw or []:
        name = str(row.get("name") or row.get("title") or "").strip()
        text = str(row.get("text") or "").strip()
        if not name and not text:
            continue
        score, full = None, None
        nums = _nums(text if ("得" in text or "/" in text or "分" in text or name) else text)
        if nums:
            score = nums[0]
            full = nums[1] if len(nums) >= 2 and nums[1] >= score else None
        weight = None
        m = re.search(r"(?:占比|权重)\D*([0-9]+(?:\.[0-9]+)?)", text)
        if m:
            weight = float(m.group(1))
        out.append({"name": name or text[:24], "kind": _kind_of(name or text),
                    "score": score, "full": full, "weight": weight})
    return out


def _num(v: Any) -> float | None:
    """把页面文本(可能带“分”“%”“名”等单位)转成数字;转不出返回 None(不臆造)。"""
    m = re.search(r"[0-9]+(?:\.[0-9]+)?", str(v if v is not None else ""))
    return float(m.group(0)) if m else None


def build_study_rows(s: dict) -> list[dict[str, Any]]:
    """把“学习记录/综合成绩”页抽出的各 ID 值,整理成成绩明细行(kind='study')。

    没有的项跳过(不编造);综合成绩本身走 overview,不进 rows。
    """
    s = s or {}
    rows: list[dict[str, Any]] = []

    def add(name: str, score: Any = None, full: Any = None, weight: Any = None) -> None:
        sc, fu, wt = _num(score), _num(full), _num(weight)
        if sc is None and fu is None and wt is None:
            return
        rows.append({"name": name, "kind": "study", "score": sc, "full": fu, "weight": wt})

    add("章节任务点完成", s.get("jobfinish"), s.get("jobpublish"))
    add("任务点完成率", s.get("jobper"))
    add("当前排名", s.get("jobrank"))
    add("课程积分", s.get("point"))
    sign = s.get("sign") or {}
    add("签到·出勤", sign.get("attendance"))
    add("签到·缺勤", sign.get("absence"))
    add("签到·迟到", sign.get("late"))
    add("签到·早退", sign.get("early"))
    add("签到·已过期", sign.get("overdue"))
    return rows


def study_overview_text(s: dict) -> str:
    """综合成绩一句话,如 “综合成绩 52.44 分”;抓不到返回空串。"""
    score = _num((s or {}).get("score"))
    return f"综合成绩 {score} 分" if score is not None else ""


# 考核标准里的权重,如“章节任务点：40%”
_CRIT_WEIGHT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%")


def parse_criteria_modules(mods: list[dict]) -> list[dict[str, Any]]:
    """解析“查看考核标准”里的各 .module:标题(含权重%)+得分规则说明。

    标题拆成名称+占比权重,规则原文放进 note(供“如何加分/讨论规则”展示)。
    """
    out: list[dict[str, Any]] = []
    for m in mods or []:
        title = re.sub(r"\s+", " ", str(m.get("title") or "")).strip()
        if not title:
            continue
        wm = _CRIT_WEIGHT_RE.search(title)
        weight = float(wm.group(1)) if wm else None
        name = re.split(r"[：:]", title)[0].strip()
        rule = re.sub(r"\s+", " ", str(m.get("rule") or "")).strip()
        out.append({"name": f"考核·{name}", "kind": _kind_of(name), "score": None,
                    "full": None, "weight": weight, "note": rule})
    return out


def merge_grade_rows(a: list[dict], b: list[dict]) -> list[dict[str, Any]]:
    """按规范名合并两组成绩行:同名保留“信息更全”的那条(分数/权重/note 非空优先)。"""
    def norm(r: dict) -> str:
        return re.sub(r"\s+", "", str(r.get("name") or "")).replace("考核·", "")

    def richness(r: dict) -> tuple:
        return (r.get("score") is not None, r.get("weight") is not None,
                bool(r.get("note")), r.get("full") is not None)

    merged: dict[str, dict] = {}
    for r in [*(a or []), *(b or [])]:
        key = norm(r)
        if not key:
            continue
        cur = merged.get(key)
        if cur is None or richness(r) > richness(cur):
            merged[key] = r
    return list(merged.values())


def grade_tips(rows: list[dict]) -> list[str]:
    """据考核权重 + 学习完成度 + 讨论规则,生成“如何加分”建议(数据驱动,缺项则不出)。"""
    rows = rows or []
    tips: list[str] = []
    covered: set[str] = set()

    def weight_of(keyword: str) -> float | None:
        for r in rows:
            if r.get("weight") and keyword in str(r.get("name", "")):
                return float(r["weight"])
        return None

    # 讨论:权重 + 规则 + 行动
    for r in rows:
        if r.get("kind") == "discussion" and (r.get("weight") or r.get("note")):
            w = r.get("weight")
            note = r.get("note") or ""
            tips.append("讨论" + (f"（占 {w:g}%）" if w else "") +
                        ("：" + note if note else "") + " → 多发话题、多回复、争取被点赞即可加分")
            covered.add("讨论")
    # 任务点完成度:用 study 行 + 考核权重,指出还差多少个
    for r in rows:
        if "任务点" in str(r.get("name", "")) and r.get("score") is not None and r.get("full"):
            sc, fu = float(r["score"]), float(r["full"])
            wt = weight_of("任务点")
            tips.append(f"任务点 {sc:g}/{fu:g}" + (f"（占 {wt:g}%）" if wt else "") +
                        f",还差 {fu - sc:g} 个未完成——补任务点通常是提分大头")
            covered.add("任务点")
            break
    # 其余有权重但还没拿到分的考核项:提醒去做
    for r in rows:
        name = str(r.get("name", ""))
        if not r.get("weight") or r.get("kind") == "study":
            continue
        if r.get("score") is not None:
            continue
        if any(c in name for c in covered):
            continue
        tips.append(f"{name.replace('考核·', '')} 占 {float(r['weight']):g}%：尽快完成并查看得分")
    return tips


def render_grades(rows: list[dict], overview: str = "") -> str:
    rows = rows or []
    lines = [f"课程成绩 ｜ {overview}" if overview else "课程成绩"]
    if not rows:
        lines.append("(未抓到成绩明细)")
        return "\n".join(lines)
    # 先列考核权重,再列学习数据
    crit = [r for r in rows if str(r.get("name", "")).startswith("考核·")]
    rest = [r for r in rows if not str(r.get("name", "")).startswith("考核·")]
    for r in crit + rest:
        bits = []
        if r.get("score") is not None:
            full = r.get("full")
            bits.append(f"{r['score']}" + (f"/{full}" if full else ""))
        if r.get("weight") is not None:
            bits.append(f"占比 {r['weight']:g}%")
        val = " ｜ ".join(bits) if bits else "无分值"
        line = f"  · {r['name']}({r['kind']})：{val}"
        if r.get("note"):
            line += f"\n      {r['note']}"
        lines.append(line)
    tips = grade_tips(rows)
    if tips:
        lines.append("如何加分：")
        lines.extend(f"  + {t}" for t in tips)
    return "\n".join(lines)
