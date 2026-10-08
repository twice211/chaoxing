# -*- coding: utf-8 -*-
"""
course.discussion —— 计分讨论：目标/规划(纯逻辑) + DiscussionRunner(浏览器操作)

纯逻辑部分不依赖浏览器,便于离线自检:
- graded_discussion_targets:只从成绩里挑“计分的讨论”(kind=discussion 且 weight>0);
- plan_discuss_actions:按“够目标即停”算出本轮还差几条发帖/回复(回复只挑未回复过的话题),受每轮上限截断;
- content_fp:内容指纹,用于发帖/回复去重,绝不灌水。
真实发帖/回复(浏览器操作)由 DiscussionRunner 承担,写操作统一过 exam/snapshot 的受闸入口。
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

from utils.logger import get_logger
from utils.text import fingerprint, one_line

log = get_logger("course.discussion")

# 讨论区话题枚举(真机:每行 .dataBody_td 内含 openDetail('<id>') 稳定标识)
TOPICS_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const rows = [];
  document.querySelectorAll('.dataBody_td, li').forEach((li) => {
    const titleEl = li.querySelector('.topicli_title_text');
    if (!titleEl) return;
    const m = (li.innerHTML || '').match(/openDetail\(['\"]([0-9a-fA-F-]{8,})['\"]\)/);
    const sn = li.querySelector('.topicli_content');
    const replied = !!li.querySelector('.topic_reply');
    rows.push({key: m ? m[1] : norm(titleEl.innerText).slice(0, 24),
               title: norm(titleEl.innerText).slice(0, 120),
               snippet: norm(sn ? sn.innerText : '').slice(0, 400), replied});
  });
  return rows.slice(0, 200);
}
"""

# 发帖核验必须读取已加载列表的完整正文和全部行；规划用 TOPICS_JS 的截断不可用于核验。
VERIFY_POSTS_JS = r"""
() => {
  const rows = [];
  document.querySelectorAll('.dataBody_td, li').forEach((li) => {
    const title = li.querySelector('.topicli_title_text');
    const body = li.querySelector('.topicli_content');
    if (!title || !body) return;
    const match = (li.innerHTML || '').match(/openDetail\(['\"]([0-9a-fA-F-]{8,})['\"]\)/);
    if (!match) return;
    rows.push({key: match[1], title: title.innerText || '', body: body.innerText || ''});
  });
  return rows;
}
"""

# 点开指定话题(openDetail('<key>'))——真机上此路“尽力而为”,不稳时调用方回退为提示手动点开
OPEN_TOPIC_JS = r"""
({key, click}) => {
  for (const el of document.querySelectorAll('.dataBody_td, li, tr, [onclick]')) {
    const html = el.innerHTML || '';
    if (html.includes('openDetail') && html.includes(key)) {
      const t = el.querySelector('.comment') || el.querySelector('[onclick]') || el;
      if (click) t.click();
      return true;
    }
  }
  return false;
}
"""


def content_fp(text: str) -> str:
    """讨论内容指纹:归一化空白后取指纹,同义重复可被去重(不灌水)。"""
    norm = " ".join(str(text or "").split())
    return fingerprint(norm, [])


def is_publish_verified(signals: dict[str, bool] | None) -> bool:
    """发布后的页面证据至少命中一项，才可把本地记录计为已发布。

    单纯的 click() 成功不构成证据：平台可能校验失败、网络中断，或按钮没有响应。
    """
    evidence = signals or {}
    return not evidence.get("failed") and any(
        bool(evidence.get(key)) for key in ("toast", "content_found"))


def publish_batch_preview(course: dict, drafts: list[dict]) -> str:
    """本轮提交的课程、条数、目标话题与完整草稿，供 CLI 和工作台共用。"""
    lines = [f"课程：{course.get('name', '')}｜本轮 {len(drafts)} 条", ""]
    for i, draft in enumerate(drafts, 1):
        kind = "发表" if draft["type"] == "new_post" else "回复"
        target = draft.get("title") or ("新话题" if kind == "发表" else draft.get("topic_key", ""))
        lines.append(f"[{i}] {kind}《{target}》\n{draft['text']}\n")
    lines.append("确认后逐条发布并核验；任一条未确认成功，本轮立即停止。")
    return "\n".join(lines)


def parse_discuss_rule(note: str, full: Any = None) -> dict[str, Any]:
    """从考核规则原文提取讨论的“分值/目标分/条数下限”;解析不出的字段回退默认,绝不臆造。

    例:「发表话题+1、回复话题+2、被赞+1,满分100」→ 发帖+1、回复+2、目标100、无条数下限。
    分值>20 视为误捕(多半把“满分100”抓成了分值),回退默认。
    """
    text = str(note or "")

    def _val(kw: str, default: float) -> float:
        m = re.search(kw + r"[^+＋。\n]{0,12}[+＋]\s*(\d+(?:\.\d+)?)", text)
        if not m:
            m = re.search(kw + r"[^。\n]{0,14}?(\d+(?:\.\d+)?)\s*分", text)
        if not m:
            return default
        v = float(m.group(1))
        return v if 0 < v <= 20 else default

    def _min(kw: str) -> int | None:
        m = re.search(kw + r"[^0-9]{0,10}(?:≥|>=|不少于|至少|不低于)\s*(\d+)", text)
        if not m:
            m = re.search(kw + r"[^。\n]{0,8}?(\d+)\s*[条次篇个]", text)
        return int(m.group(1)) if m else None

    m = re.search(r"满分\s*(\d+(?:\.\d+)?)", text)
    target = float(m.group(1)) if m else (float(str(full)) if full not in (None, "") else 100.0)
    return {"post_value": _val(r"(?:发表|发帖|发布话题|新话题)", 1.0),
            "reply_value": _val(r"(?:回复|回帖|回答)", 2.0),
            "target": target,
            "post_min": _min(r"(?:发表|发帖|发布话题|新话题)"),
            "reply_min": _min(r"(?:回复|回帖|回答)")}


def recommend_discuss_max(rule: dict, done: dict, topics: list[dict],
                          current_score: float | None = None) -> int:
    """按“还差多少分”估算最少需要的发帖+回复总条数,作为每轮上限(DISCUSS_MAX)的推荐值。

    优先用未回复话题凑(回复分值通常更高),不足再用发帖补;返回 0 表示已达标无需再发。
    """
    pv = float((rule or {}).get("post_value") or 1)
    rv = float((rule or {}).get("reply_value") or 2)
    target = float((rule or {}).get("target") or 100)
    done_p = int((done or {}).get("new_posts") or 0)
    done_r = int((done or {}).get("replies") or 0)
    cur = float(current_score) if current_score is not None else done_p * pv + done_r * rv
    rem = max(0.0, target - cur)
    free = sum(1 for t in (topics or []) if not t.get("replied"))
    n_rep = min(free, int(-(-rem // rv))) if rv > 0 else 0
    rem2 = max(0.0, rem - n_rep * rv)
    n_post = int(-(-rem2 // pv)) if pv > 0 and rem2 > 0 else 0
    gap = (max(0, int((rule or {}).get("post_min") or 0) - done_p)
           + max(0, int((rule or {}).get("reply_min") or 0) - done_r))
    return max(n_rep + n_post, gap)


def parse_batch_output(raw: str) -> dict[int, str]:
    """拆分批量生成文本:以单独一行 ###N### 为界(**包裹/前后空格容错),返回 {序号: 该条正文}。"""
    text = str(raw or "")
    marks = list(re.finditer(r"^\s*\*{0,3}\s*#{2,}\s*(\d+)\s*#{2,}\s*\*{0,3}\s*$", text, re.M))
    out: dict[int, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        seg = text[m.end():end].strip()
        if seg:
            out[int(m.group(1))] = seg
    return out


# 发帖“提问角度”清单:批量生成时逐条指定不同切入面,防同批雷同
DISCUSS_ANGLES = ("概念理解的困惑点", "某现象背后的原因", "某决策的长远影响", "与当下现实的联系",
                  "不同观点/做法的比较", "课程方法如何迁移", "值得反思的一个细节")


def build_discuss_batches(acts: list[dict], topic_by_key: dict,
                          reply_batch: int = 5, post_batch: int = 10) -> list[dict[str, Any]]:
    """把动作切成 AI 批量调用的组:回复每批≤5且各带自己的话题(天然不重复);发帖每批≤10。"""
    reply_idx = [i for i, a in enumerate(acts) if a.get("type") == "reply"]
    post_idx = [i for i, a in enumerate(acts) if a.get("type") != "reply"]
    batches: list[dict[str, Any]] = []
    rb = max(1, int(reply_batch or 5))
    for s in range(0, len(reply_idx), rb):
        chunk = reply_idx[s:s + rb]
        batches.append({"kind": "reply", "idxs": chunk,
                        "topics": [topic_by_key.get(str(acts[i].get("topic_key") or ""), {}) for i in chunk]})
    pb = max(1, int(post_batch or 10))
    for s in range(0, len(post_idx), pb):
        batches.append({"kind": "new_post", "idxs": post_idx[s:s + pb]})
    return batches


def split_question_post(raw: str) -> tuple[str, str]:
    """AI 发帖输出约定=首行问题标题+空行+正文;首行过长或仅一段则整段作正文、标题留空。"""
    lines = [ln.strip() for ln in str(raw or "").splitlines() if ln.strip()]
    if not lines:
        return "", ""
    if len(lines) >= 2 and len(lines[0]) <= 40:
        return lines[0], "\n".join(lines[1:])
    return "", "\n".join(lines)


def _contains(el: Any, other: Any) -> bool:
    """el 是否为 other 的祖先(同选择器命中“按钮+父容器”时用来去重)。"""
    try:
        return bool(el.evaluate("(n, o) => n.contains(o)", other))
    except Exception:
        return False


def discuss_write_allowed(cfg: Any, guard: Any, url: str) -> tuple[bool, str]:
    """三道门全过才允许发帖/回复；任一不过返回 (False, 原因)。纯逻辑、可离线测。

    ① PRACTICE_MODE ② PRACTICE_ALLOW_DISCUSS_POST(默认关) ③ guard.allow_page_write(非考试页可写)。
    """
    if not cfg.get("PRACTICE_MODE"):
        return False, "未开启“练习/演示模式”，不自动发帖/回复。"
    if not cfg.get("PRACTICE_ALLOW_DISCUSS_POST"):
        return False, "未开启“允许自动发帖回复”(PRACTICE_ALLOW_DISCUSS_POST)，仅可预览草稿。"
    try:
        ok = bool(guard.allow_page_write(url, want_submit=False)) if guard is not None else False
    except Exception:
        ok = False
    if not ok:
        return False, "当前页被判定为考试页/不可写，按约定不自动发布。"
    return True, ""


def discuss_submit_allowed(cfg: Any, guard: Any, url: str) -> tuple[bool, str]:
    """自动“点发表/回复”的第四道门:三道门全过 + PRACTICE_ALLOW_DISCUSS_SUBMIT(默认关)。

    公开不可逆,故比“填入”再多一道显式开关;关着时保持半自动(只填不点)。
    """
    ok, why = discuss_write_allowed(cfg, guard, url)
    if not ok:
        return False, why
    if not cfg.get("PRACTICE_ALLOW_DISCUSS_SUBMIT"):
        return False, "未开启“允许自动提交发表/回复”(PRACTICE_ALLOW_DISCUSS_SUBMIT)，填入后仍需你亲自点发表。"
    return True, ""


def graded_discussion_targets(grade_rows: list[dict], weight_min: float = 0.0) -> list[dict[str, Any]]:
    """从成绩行挑“计分的讨论”：kind=discussion 且权重 > weight_min。不计分的一律不碰。"""
    out: list[dict[str, Any]] = []
    for r in grade_rows or []:
        if str(r.get("kind")) == "discussion" and float(r.get("weight") or 0) > weight_min:
            out.append({"name": r.get("name", "讨论"), "weight": float(r.get("weight") or 0),
                        "rule": r.get("note", ""), "score": r.get("score"), "full": r.get("full"),
                        "synced_at": r.get("synced_at", ""),
                        "credit_base_score": r.get("credit_base_score"),
                        "credit_base_at": r.get("credit_base_at", "")})
    return out


def plan_discuss_actions(rule: dict, done: dict, topics: list[dict], max_this_round: int,
                         current_score: float | None = None) -> list[dict[str, Any]]:
    """按“凑够目标分”自由组合发帖/回复；够分即停、受每轮上限截断。纯逻辑、可离线测。

    优先级:①规则写明“发表≥n条”的缺口(两类都要区分完成) ②规则写明“回复≥m条”的缺口
    ③剩余分差:回复分值高且同话题不重复计分→优先回复未回复过的话题,
      分差用帖子补(小额分差/话题用尽)。
    rule=parse_discuss_rule 输出；done={"new_posts","replies"}；topics=[{"key","replied"}]；
    current_score=平台当前讨论分(缺省时按本地条数×分值估算,不含被赞)。
    """
    pv = float((rule or {}).get("post_value") or 1)
    rv = float((rule or {}).get("reply_value") or 2)
    target = float((rule or {}).get("target") or 100)
    cap = max(0, int(max_this_round or 0))
    acts: list[dict[str, Any]] = []
    if cap <= 0:
        return acts
    done_p = int((done or {}).get("new_posts") or 0)
    done_r = int((done or {}).get("replies") or 0)
    cur = float(current_score) if current_score is not None else done_p * pv + done_r * rv
    need = max(0.0, target - cur)
    free = [str(t.get("key")) for t in (topics or []) if not t.get("replied") and t.get("key")]
    want_posts = max(0, int((rule or {}).get("post_min") or 0) - done_p)
    want_replies = max(0, int((rule or {}).get("reply_min") or 0) - done_r)
    while len(acts) < cap:
        if want_posts > 0:
            acts.append({"type": "new_post"})
            want_posts -= 1
            need = max(0.0, need - pv)
        elif want_replies > 0 and free:
            acts.append({"type": "reply", "topic_key": free.pop(0)})
            want_replies -= 1
            need = max(0.0, need - rv)
        elif need > 0 and free and rv >= pv:
            acts.append({"type": "reply", "topic_key": free.pop(0)})
            need = max(0.0, need - rv)
        elif need > 0:
            acts.append({"type": "new_post"})
            need = max(0.0, need - pv)
        else:
            break
    return acts


class DiscussionRunner:
    """计分讨论自动参与：抓取话题 + 用 AI 发帖/回复(写操作经快照受闸)。浏览器操作实现见后续。"""

    def __init__(self, browser: Any, cfg: Any, store: Any, crawler: Any,
                 engine: Any = None, snap: Any = None) -> None:
        self.browser = browser
        self.cfg = cfg
        self.store = store
        self.crawler = crawler
        self.engine = engine
        self.snap = snap
        self.last_estimate: dict = {}       # 最近一次 prepare_drafts 的估算 {"rec": 推荐每轮条数, "remaining": 剩余分}
        self.last_errors: list[str] = []    # 最近一次 generate_drafts 的真实失败原因(供界面显形,不再静默吞错)
        self._topic_board_frame: Any = None
        self._topic_board_url = ""
        self._active_reply_frame: Any = None
        self._active_reply_page: Any = None
        self._active_reply_key = ""
        self._active_reply_editor: Any = None

    # ------------------------------------------------------------ 只读:进入讨论区 + 导出结构
    def open_board(self, page: Any, course: dict) -> bool:
        """进入课程独立的“讨论”区(点顶部/左侧“讨论”标签)。只读,不写任何东西。"""
        from browser import actions as A
        self._topic_board_frame, self._topic_board_url = None, ""
        self._active_reply_frame = None
        self._active_reply_page = None
        self._active_reply_key = ""
        self._active_reply_editor = None
        dest = course.get("url") or self.store.get_meta(f"course_url:{course['id']}", "")
        if dest:
            A.safe_goto(page, dest, attempts=2)
        A.close_popups(page)
        return bool(self.crawler._open_nav_tab(page, ("讨论", "话题", "讨论区")))

    def list_topics(self, page: Any) -> list[dict]:
        """只读:枚举讨论区话题 [{key,title,snippet}]。"""
        from browser import actions as A
        out: list[dict] = []
        for frame in [page, *A.frames_of(page)]:
            try:
                rows = A.js_eval(frame, TOPICS_JS)
            except Exception:
                rows = None
            if rows:
                for r in rows:
                    if str(r.get("title") or "").strip():
                        out.append(r)
                if out:
                    self._topic_board_frame = getattr(frame, "main_frame", frame)
                    self._topic_board_url = str(self._topic_board_frame.url or "")
                break
        return out

    def published_post_visible(self, page: Any, course: dict, title: str, fp: str) -> bool:
        """当前课程已加载列表中仅有一条同标题、同完整正文的话题时确认。"""
        from browser import actions as A
        parts = str(course.get("course_key") or "").split("_")
        if len(parts) != 3 or not title or not fp:
            return False
        expected = {"courseid": parts[0], "cpi": parts[1], "clazzid": parts[2]}
        norm_title = "".join(str(title).split())
        for frame in A.frames_of(page):
            url = str(frame.url or "")
            if "topicList" not in url:
                continue
            query = parse_qs(urlsplit(url).query)
            if any(query.get(key, [""])[0] != value for key, value in expected.items()):
                continue
            rows = A.js_eval(frame, VERIFY_POSTS_JS) or []
            matches = [row for row in rows
                       if "".join(str(row.get("title") or "").split()) == norm_title
                       and content_fp(row.get("body") or "") == fp
                       and re.fullmatch(r"[0-9a-fA-F-]{8,}", str(row.get("key") or ""))]
            return len(matches) == 1
        return False

    def compute_plan(self, page: Any, course: dict, replied_keys: set[str],
                     kind: str | None = None) -> tuple[list[dict], str, dict]:
        """只读规划:返回(本轮动作, 摘要, 草稿上下文ctx)。与 make_draft 拆开便于逐条协作式生成。

        kind=None=两类都规划;kind="new_post"/"reply"=只出该模块草稿(共用同一份凑分规划)。
        """
        cid = int(course["id"])
        targets = graded_discussion_targets(self.store.list_grades(cid))
        if not targets:
            return [], "", {}
        row = targets[0]
        rule = parse_discuss_rule(row.get("rule", ""), row.get("full"))
        topics = self.list_topics(page)
        for t in topics:
            t["replied"] = bool(t.get("replied")) or str(t.get("key") or "") in (replied_keys or set())
        done = self.store.discuss_done_counts(cid)
        if row.get("score") is not None:
            # 成绩刷新可能仍返回旧分数：从首次观测基线累加已确认发布，
            # 与平台最新分数取较大值，避免刷新缓存后把待计分内容遗忘并重复发帖。
            since = str(row.get("credit_base_at") or row.get("synced_at") or "")
            recent = [d for d in self.store.list_discussions(cid, limit=10000)
                      if d["status"] == "posted" and
                      (not since or str(d.get("posted_at") or d.get("created_at") or "") > since)]
            local_estimate = float(row.get("credit_base_score") if row.get("credit_base_score") is not None
                                   else row["score"]) + sum(
                rule["post_value"] if d["kind"] == "new_post" else rule["reply_value"] for d in recent)
            cur = max(float(row["score"]), local_estimate)
        else:
            cur = done["new_posts"] * rule["post_value"] + done["replies"] * rule["reply_value"]
        acts_all = plan_discuss_actions(rule, done, topics, int(self.cfg.get("DISCUSS_MAX") or 5),
                                        current_score=cur)
        acts = [a for a in acts_all if kind is None or a["type"] == kind]
        n_post = sum(1 for a in acts_all if a["type"] == "new_post")
        n_reply = len(acts_all) - n_post
        proj = n_post * rule["post_value"] + n_reply * rule["reply_value"]
        rec = recommend_discuss_max(rule, done, topics, current_score=cur)
        self.last_estimate = {"rec": rec, "remaining": max(0.0, rule["target"] - cur)}
        summary = (f"讨论计分：{row['name']}（占{row['weight']:g}%）｜规则：发帖+{rule['post_value']:g}、"
                   f"回复+{rule['reply_value']:g}、目标{rule['target']:g}分｜当前约{cur:g}分｜"
                   f"本轮整轮 {n_post} 发帖 + {n_reply} 回复（预计+{proj:g}分）"
                   + ("" if acts_all else "｜已达目标，无需再发")
                   + (f"｜推荐每轮上限 {rec} 条（按剩余分一次凑满）" if rec > 0 else ""))
        ctx = {"rule": rule, "topic_by_key": {str(t.get("key")): t for t in topics},
               "n_post": n_post, "n_reply": n_reply, "reached": not acts_all}
        return acts, summary, ctx

    def draft_from_text(self, act: dict, text: str, ctx: dict) -> dict | None:
        """把一段生成文本按动作(发帖拆标题/回复挂话题)组装成草稿;空文本→None。"""
        text = str(text or "").strip()
        if not text:
            return None
        if act["type"] == "new_post":
            title, body = split_question_post(text)
            if not body:
                return None
        else:
            t = (ctx or {}).get("topic_by_key", {}).get(str(act.get("topic_key") or ""), {})
            title = t.get("title", "")
            body = text
        return {"type": act["type"], "topic_key": str(act.get("topic_key") or ""),
                "title": title, "text": body, "fp": content_fp(body)}

    def make_draft(self, act: dict, ctx: dict, course: dict) -> dict | None:
        """单条生成(批量缺号时的回退路径,也是 AI 关闭时的判断口径)。"""
        if act["type"] == "new_post":
            return self.draft_from_text(act, self._draft("", course, kind="new_post"), ctx)
        t = (ctx or {}).get("topic_by_key", {}).get(str(act.get("topic_key") or ""), {})
        return self.draft_from_text(act, self._draft(t.get("title", ""), course, kind="reply",
                                                     excerpt=t.get("snippet", "")), ctx)

    def _ai_on(self) -> bool:
        return bool(self.engine is not None and getattr(self.engine, "ai", None) is not None
                    and getattr(self.engine.ai, "enabled", False))

    def _batch_map(self, batch: dict, course: dict, acts: list[dict], ctx: dict) -> dict[int, dict]:
        """一次批量调用→{动作下标: 草稿}。回复每批绑定各自话题,天然不重复;发帖按角度清单防雷同。"""
        idxs = batch["idxs"]
        cname = course.get("name", "")
        if batch["kind"] == "reply":
            listing = "\n".join(
                f"话题{j + 1}《{one_line(t.get('title', ''))[:60]}》摘要：{(t.get('snippet') or '')[:300]}"
                for j, t in enumerate(batch.get("topics") or []))
            raw = self.engine.discussion_batch_text("reply", cname, len(idxs), listing=listing)
        else:
            angles = "；".join(f"第{j + 1}条：{DISCUSS_ANGLES[j % len(DISCUSS_ANGLES)]}" for j in range(len(idxs)))
            raw = self.engine.discussion_batch_text("new_post", cname, len(idxs), angles=angles)
        segs = parse_batch_output(raw)
        out: dict[int, dict] = {}
        for pos, i in enumerate(idxs, 1):
            d = self.draft_from_text(acts[i], segs.get(pos, ""), ctx) if pos in segs else None
            if d:
                out[i] = d
        return out

    def generate_drafts(self, acts: list[dict], ctx: dict, course: dict,
                        on_partial: Any = None, should_stop: Any = None) -> list[dict]:
        """批量+并行生成草稿(提速核心)。返回按 acts 顺序、指纹去重后的草稿列表。

        on_partial(drafts, total) 供 UI 增量刷新;should_stop() 置位后不再补发新调用。
        批内缺号/截断 → 该条自动回退单条生成;AI 关闭 → []。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        self.last_errors = []
        def _note(msg: Any) -> None:
            s = str(msg or "")[:200]
            if s and len(self.last_errors) < 8 and s not in self.last_errors:
                self.last_errors.append(s)
        if not acts:
            return []
        if not self._ai_on():
            _note("AI 未启用（AI_ENABLE=False 或密钥未配）,不会生成草稿。")
            return []
        results: dict[int, dict] = {}
        extras: list[dict] = []
        batches = build_discuss_batches(acts, ctx.get("topic_by_key", {}))
        workers = max(1, min(int(self.cfg.get("DISCUSS_AI_PARALLEL") or 4), 8, len(batches)))

        def _dedup(items: list[dict]) -> list[dict]:
            seen: set[str] = set()
            out: list[dict] = []
            dropped = 0
            for d in items:
                if d["fp"] in seen:
                    dropped += 1
                    continue
                seen.add(d["fp"])
                out.append(d)
            if dropped:
                _note(f"有 {dropped} 条草稿内容雷同被去重丢弃（模型跨批复读）")
            return out

        def _publish():
            if on_partial:
                on_partial(_dedup([v for _k, v in sorted(results.items())] + extras), len(acts))

        ex = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="discuss-batch")
        try:
            futs = [ex.submit(self._batch_map, b, course, acts, ctx) for b in batches]
            for f in as_completed(futs):
                try:
                    results.update(f.result())
                except Exception as exc:
                    log.warning("批量草稿一组失败：%s", exc)
                    _note(exc)
                _publish()
            if not results and batches:
                _note(f"{len(batches)} 次批量调用都没拆出内容(模型未按 ###N### 分隔或输出被截断)→自动转单条逐条生成")
            missing = [i for i in range(len(acts)) if i not in results]
            if missing and not (should_stop and should_stop()):
                futs2 = [ex.submit(self.make_draft, acts[i], ctx, course) for i in missing]
                for f in as_completed(futs2):
                    if should_stop and should_stop():
                        break
                    try:
                        d = f.result()
                    except Exception as exc:
                        log.warning("单条补生成失败：%s", exc)
                        _note(exc)
                        d = None
                    if d:
                        extras.append(d)
                    _publish()
                if not results and not extras and missing:
                    _note("单条补生成也没有产出：检查 AI 密钥/额度/网络（日志 logs/ 有详情）")
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        return _dedup([v for _k, v in sorted(results.items())] + extras)

    def prepare_drafts(self, page: Any, course: dict, replied_keys: set[str],
                       kind: str | None = None) -> tuple[list[dict], str]:
        """只读预览(阻塞式,CLI 用):规划 + 批量并并行生成草稿(工作台走 compute_plan+generate_drafts)。"""
        acts, summary, ctx = self.compute_plan(page, course, replied_keys, kind=kind)
        return self.generate_drafts(acts, ctx, course), summary

    def _draft(self, topic: str, course: dict, kind: str, excerpt: str = "") -> str:
        if self.engine is None or not getattr(self.engine, "ai", None) or not getattr(self.engine.ai, "enabled", False):
            return ""
        try:
            return self.engine.discussion_text(
                topic, course=course.get("name", ""), chapter="", excerpt=excerpt, kind=kind)
        except Exception as exc:
            log.debug("讨论草稿生成失败：%s", exc)
            return ""

    # ------------------------------------------------------------ 半自动填入(绝不替你点“发表/回复”)
    def _restore_topic_board(self, page: Any) -> bool:
        """从已打开的话题详情返回生成草稿时确认过的讨论列表。"""
        from browser import actions as A
        frame, url = self._topic_board_frame, self._topic_board_url
        if not frame or not url.startswith(("https://", "http://")):
            return False
        if frame not in A.frames_of(page):
            return False
        try:
            if str(frame.url or "") != url:
                frame.goto(url, wait_until="domcontentloaded", timeout=10000)
            return True
        except Exception as exc:
            log.debug("返回讨论列表失败：%s", exc)
            return False

    def open_new_post(self, page: Any) -> bool:
        """点击“新建话题”,打开发帖表单(标题框+正文编辑器)。只打开,不发布。"""
        from browser import actions as A
        self._active_reply_frame = None
        self._active_reply_page = None
        self._active_reply_key = ""
        self._active_reply_editor = None
        if any("replyslist" in str(f.url or "").lower() for f in A.frames_of(page)):
            self._restore_topic_board(page)
        _fr, btn = A.in_frames(page, "disc_new_post_btn")
        if btn is None:
            return False
        try:
            btn.evaluate("node => node.click()")
            return True
        except Exception as exc:
            log.debug("打开新建话题失败：%s", exc)
            return False

    def open_topic_reply(self, page: Any, topic_key: str, timeout_sec: float = 3.0) -> bool:
        """打开指定话题；只有目标话题的详情页实际加载后才允许继续填写。"""
        from browser import actions as A
        import time as _t
        key = str(topic_key or "")
        if not key:
            return False
        self._active_reply_frame = None
        self._active_reply_page = None
        self._active_reply_key = ""
        self._active_reply_editor = None
        context = getattr(page, "context", None)
        existing_pages = set(context.pages) if context is not None else set()
        already_open = any(self._reply_url_matches(f.url, key) for f in A.frames_of(page))
        if not already_open:
            clicked = False
            for attempt in range(2):
                for frame in A.frames_of(page):
                    if A.js_eval(frame, OPEN_TOPIC_JS, {"key": key, "click": False}):
                        # 导航可能在 click() 返回前销毁旧 frame；预先确认候选后仍等待详情 URL。
                        A.js_eval(frame, OPEN_TOPIC_JS, {"key": key, "click": True})
                        clicked = True
                        break
                if clicked or attempt or not self._restore_topic_board(page):
                    break
                page.wait_for_timeout(300)
            if not clicked:
                return False
        # 学习通的 openDetail 会 window.open 新标签；仅核验本次新开的页或原页。
        deadline = _t.monotonic() + max(0.0, timeout_sec)
        while True:
            pages = [page]
            if context is not None:
                pages.extend(pg for pg in context.pages if pg not in existing_pages and not pg.is_closed())
            for candidate in pages:
                for frame in A.frames_of(candidate):
                    if not self._reply_url_matches(frame.url, key):
                        continue
                    try:
                        editors = [el for el in frame.query_selector_all(
                            "textarea[placeholder*='回复话题'], textarea[name='replyContent'], "
                            "textarea#replyContent, [contenteditable='true']#replyContent") if el.is_visible()]
                        if len(editors) == 1:
                            self._active_reply_frame = frame
                            self._active_reply_page = candidate
                            self._active_reply_key = key
                            self._active_reply_editor = editors[0]
                            return True
                    except Exception:
                        pass
            if _t.monotonic() >= deadline:
                return False
            # Playwright 同步 API 需要一次调用来处理导航事件；time.sleep 会卡住旧 URL。
            page.wait_for_timeout(200)

    @staticmethod
    def _reply_url_matches(url: str, key: str) -> bool:
        """只接受实际话题详情路径；查询参数里出现目标路径不构成核验。"""
        try:
            path = urlsplit(str(url or "")).path
        except ValueError:
            return False
        return bool(key and re.fullmatch(
            rf"/course/topic/v3/bbs/[^/]+/{re.escape(key)}/replysList/?", path, re.I))

    def _reply_target_ready(self, page: Any) -> bool:
        """每次写入前重新核对话题路径及已确认的回复输入框。"""
        from browser import actions as A
        frame, editor = self._active_reply_frame, self._active_reply_editor
        try:
            return bool(page is self._active_reply_page and not page.is_closed()
                        and frame in A.frames_of(page)
                        and self._reply_url_matches(frame.url, self._active_reply_key)
                        and editor is not None and editor.is_visible()
                        and editor.evaluate("n => n.isConnected"))
        except Exception:
            return False

    def reply_page(self, board_page: Any) -> Any:
        """返回已核验的目标页；新标签若已关闭，不得回退到讨论列表编辑器。"""
        page = self._active_reply_page or board_page
        if self._active_reply_key and not self._reply_target_ready(page):
            return None
        return None if page.is_closed() else page

    def _pick_editor(self, page: Any) -> tuple[Any, str]:
        """跨 frame 收集“可见编辑框”,优先空的 contenteditable 且取最靠后(=刚弹出的回复/发帖框)。"""
        from browser import actions as A
        if self._active_reply_key:
            if not self._reply_target_ready(page):
                return None, ""
            editor = self._active_reply_editor
            return editor, str(editor.evaluate("n => n.tagName")).lower()
        sels = ["div[contenteditable='true']", "body[contenteditable='true']",
                "textarea[name*='content' i]", "textarea"]
        cands: list[tuple[int, int, Any, str]] = []
        order = 0
        frames = A.frames_of(page)
        if self._active_reply_frame in frames:
            frames = [self._active_reply_frame]
        for frame in frames:
            for sel in sels:
                try:
                    els = frame.query_selector_all(sel)
                except Exception:
                    els = []
                for el in els:
                    order += 1
                    try:
                        if not el.is_visible():
                            continue
                        tag = str(el.evaluate("n => n.tagName") or "").lower()
                        is_ce = tag not in ("textarea", "input")
                        empty = True
                        try:
                            cur = str(el.evaluate("n => n.innerText || n.value || ''") or "")
                            empty = len(cur.strip()) < 2
                        except Exception:
                            pass
                        rank = (2 if (is_ce and empty) else 1 if is_ce else 0)
                        cands.append((rank, order, el, tag))
                    except Exception:
                        continue
        if not cands:
            return None, ""
        _rank, _order, el, tag = max(cands, key=lambda c: (c[0], c[1]))
        return el, tag

    def fill_editor(self, page: Any, text: str, title: str = "") -> dict[str, bool]:
        """把草稿填进“当前可见”的发帖标题框 + 正文编辑器(含 UEditor 内层 iframe)。

        只填入、**不点发表/回复**;填完由你核对并亲自发布(或在开启自动提交后由程序点)。返回 {"title","body"}。
        """
        from browser import actions as A
        res = {"title": False, "body": False}
        if title:
            _fr, tel = A.in_frames(page, "disc_post_title")
            if tel is not None:
                try:
                    tel.fill(title)
                    res["title"] = True
                except Exception as exc:
                    log.debug("填标题失败：%s", exc)
        el, tag = self._pick_editor(page)
        if el is not None:
            try:
                if tag in ("textarea", "input"):
                    el.fill(text)
                else:
                    el.evaluate(r"""(n, t) => {
                        const html = t.split(/\n+/).filter(Boolean).map(s => {
                            const p = document.createElement('p'); p.textContent = s;
                            return p.outerHTML;
                        }).join('');
                        let editor = null;
                        try {
                            const frameId = n.ownerDocument.defaultView.frameElement?.id || '';
                            const suffix = frameId.match(/^ueditor_(\d+)$/)?.[1];
                            if (suffix !== undefined)
                                editor = window.parent.UE?.instants?.['ueditorInstant' + suffix];
                        } catch (_) {}
                        if (editor && typeof editor.setContent === 'function') {
                            editor.setContent(html);
                        } else {
                            n.innerHTML = html;
                            n.focus();
                            for (const type of ['input', 'change', 'keyup'])
                                n.dispatchEvent(new Event(type, {bubbles: true}));
                        }
                    }""", text)
                    el.evaluate("n => { try{n.dispatchEvent(new Event('blur',{bubbles:true}));}catch(e){} }")
                res["body"] = True
            except Exception as exc:
                log.debug("填入正文失败：%s", exc)
        return res

    def submit_visible(self, page: Any, kind: str) -> tuple[bool, str]:
        """点击“唯一可见”的发表/回复按钮(自动提交用)。找不到/歧义/点击失败 → (False,原因),交回手动。"""
        from browser import actions as A
        from course.selectors import selectors
        if kind == "reply" and self._active_reply_key and not self._reply_target_ready(page):
            return False, "目标话题页或回复框已变化，未提交；请重新打开目标话题"
        key = "disc_post_submit" if kind == "new_post" else "disc_reply_submit"
        for sel in selectors(key):
            found: list[Any] = []
            frames = A.frames_of(page)
            if kind == "reply" and self._active_reply_frame in frames:
                frames = [self._active_reply_frame]
            for frame in frames:
                try:
                    els = frame.query_selector_all(sel)
                except Exception:
                    continue
                for el in els:
                    try:
                        if el.is_visible():
                            found.append(el)
                    except Exception:
                        continue
            if not found:
                continue
            # 同选择器可能命中“按钮+含按钮的父容器”:只留最内层,仍多于一个则不敢盲点
            inner: list[Any] = []
            for el in found:
                if any(el is not other and _contains(el, other) for other in found):
                    continue
                inner.append(el)
            if len(inner) != 1:
                return False, f"提交按钮有 {len(found)} 个候选、无法唯一确定，请手动点击发表/回复"
            try:
                inner[0].evaluate("node => node.click()")
                return True, ""
            except Exception as exc:
                return False, f"提交按钮点击失败（{exc}），请手动点击发表/回复"
        return False, "没找到可见的发表/回复按钮，请手动点击发表/回复"

    def _publish_evidence(self, page: Any, text: str) -> dict[str, Any]:
        """只读取可见提示与编辑器外的正文，用发布前后的变化排除旧提示和草稿本身。"""
        from browser import actions as A
        evidence: dict[str, Any] = {"messages": set(), "matches": 0}
        for frame in A.frames_of(page):
            try:
                result = frame.evaluate(r"""(text) => {
                    const norm = s => (s || '').replace(/\s+/g, '').trim();
                    const visible = n => !!(n.getClientRects().length &&
                        getComputedStyle(n).visibility !== 'hidden');
                    const editing = n => n.closest('[contenteditable],textarea,input,.edui-editor');
                    const messages = [...document.querySelectorAll(
                        '[role="alert"],[role="status"],.layui-layer-content,.toast,.message,.tips')]
                        .filter(n => visible(n) && !editing(n)).map(n => n.innerText.trim());
                    const target = norm(text);
                    const matches = target ? [...document.querySelectorAll('body *')].filter(n =>
                        visible(n) && !editing(n) && norm(n.innerText).includes(target) &&
                        !n.querySelector('[contenteditable],textarea,input,iframe') &&
                        ![...n.children].some(c => norm(c.innerText).includes(target))).length : 0;
                    return {messages, matches};
                }""", text)
                evidence["messages"].update(result["messages"])
                evidence["matches"] += int(result["matches"])
            except Exception as exc:
                log.debug("读取发布证据失败：%s", exc)
        return evidence

    def publish_gen(self, page: Any, kind: str, text: str, guard: Any,
                    timeout_sec: float = 5.0, stop_event: Any = None) -> Any:
        """点击一次并协作式核验；返回 (posted|pending_verify|draft, 原因)。

        yield 的值为下一次轮询的单调时钟时刻，工作台可继续处理取消命令。
        调用方须先持久化待核验状态，避免点击或等待期间中断导致自动重发。
        """
        if stop_event is not None and stop_event.is_set():
            return "draft", "已取消，未提交"
        allowed, why = discuss_submit_allowed(self.cfg, guard, str(page.url or ""))
        if not allowed:
            return "draft", why
        before = self._publish_evidence(page, text)
        # 页面读取期间用户可能关闭权限或取消任务，点击前再次检查。
        allowed, why = discuss_submit_allowed(self.cfg, guard, str(page.url or ""))
        if not allowed or (stop_event is not None and stop_event.is_set()):
            return "draft", why or "已取消，未提交"
        clicked, why = self.submit_visible(page, kind)
        if not clicked:
            return ("pending_verify" if "点击失败" in why else "draft"), why
        deadline = time.monotonic() + max(0.0, timeout_sec)
        while True:
            after = self._publish_evidence(page, text)
            fresh = after["messages"] - before["messages"]
            signals = {
                "failed": any(re.search(r"(?:发表|发布|回复|提交|操作)失败|网络异常|不能为空|请填写", msg) for msg in fresh),
                "toast": any(re.search(r"(?:发表|发布|回复|提交|操作)成功|成功(?:发表|发布|回复|提交)", msg) for msg in fresh),
                "content_found": after["matches"] > before["matches"],
            }
            if is_publish_verified(signals):
                return "posted", "页面已确认发布成功"
            if signals["failed"]:
                return "pending_verify", "页面提示发布失败，请核对后手动处理"
            if stop_event is not None and stop_event.is_set():
                return "pending_verify", "点击后已取消，发布结果待核验"
            if time.monotonic() >= deadline:
                return "pending_verify", "已点击但未捕获发布成功证据，请在浏览器核验，勿重复提交"
            yield time.monotonic() + 0.2

    def publish(self, page: Any, kind: str, text: str, guard: Any,
                timeout_sec: float = 5.0, stop_event: Any = None) -> tuple[str, str]:
        """命令行驱动同一核验流程；工作台直接使用 publish_gen 保持可取消。"""
        gen = self.publish_gen(page, kind, text, guard, timeout_sec, stop_event)
        while True:
            try:
                wake_at = next(gen)
            except StopIteration as done:
                return done.value
            time.sleep(max(0.0, wake_at - time.monotonic()))

    def _dump_all_pages(self, page: Any, tag: str, settle: float = 3.5) -> list[str]:
        """导出当前页 + 所有相关 frame/新标签的结构(仅浏览,绝不点发布)。"""
        import time as _t
        from browser import actions as A
        _t.sleep(settle)
        pages = [page]
        ctx = getattr(page, "context", None)
        if ctx is not None:
            for pg in ctx.pages:
                if pg is page:
                    continue
                try:
                    pg.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                pages.append(pg)
        return [str(self.browser.dump_html(pg, tag)) for pg in pages]

    def capture(self, page: Any, course: dict) -> list[str]:
        """只读导出讨论区结构:话题列表 + 新建话题表单 + 第一条话题详情(回复框)。
        全程只“打开”,绝不点“发表/回复”。"""
        from browser import actions as A
        self.open_board(page, course)
        paths = [str(self.browser.dump_html(page, "discussion_board"))]
        # 1) 打开“新建话题”表单(标题框+正文编辑器+发布按钮所在页),导出后关闭
        try:
            _fr, btn = A.in_frames(page, "disc_new_post_btn")
            if btn is not None:
                btn.evaluate("node => node.click()")
                paths += self._dump_all_pages(page, "discussion_newpost")
                try:
                    page.keyboard.press("Escape")   # 关闭发帖弹层(不发布)
                except Exception:
                    pass
        except Exception as exc:
            log.debug("打开新建话题表单失败：%s", exc)
        # 2) 进入第一条话题详情(点 .comment 触发 openDetail),导出回复框
        try:
            _fr, opener = A.in_frames(page, "disc_topic_open")
            if opener is None:
                _fr, opener = A.in_frames(page, "disc_topic_link")
            if opener is not None:
                opener.evaluate("node => node.click()")
                paths += self._dump_all_pages(page, "discussion_topic")
        except Exception as exc:
            log.debug("进入话题详情失败：%s", exc)
        return paths
