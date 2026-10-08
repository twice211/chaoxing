# -*- coding: utf-8 -*-
"""
exam.guard —— 考试 AI 模式的合规闸门

三重开关（缺一不可）：
  1) user_config.py 里 EXAM_MODE_ALLOWED = True（你确认课程允许开卷且允许 AI）；
  2) 规则文本扫描：EXAM_RULE_TEXT 或考试页面上的说明文字，一旦命中
     “禁止使用 AI / 闭卷 / 不得参考资料”等表述 → 自动关闭考试 AI 模式；
  3) 运行时二次确认：必须亲手输入 “课程名 + 确认口令” 才能启用。

本模块同时是所有“禁止类操作”的拒绝点：任何写页面/绕限制的请求都会被记录并拒绝。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from utils.console import confirm, err, info, ok, warn
from utils.logger import get_logger

log = get_logger("exam.guard")

FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("禁止使用 AI / 人工智能", re.compile(r"(禁止|不得|不允许|严禁)[^。；\n]{0,12}(AI|ai|人工智能|大模型|ChatGPT)")),
    ("闭卷考试", re.compile(r"(闭卷|严格闭卷)")),
    ("不得查阅资料", re.compile(r"(不得|禁止|不允许)[^。；\n]{0,8}(查阅|携带|参考|使用)[^。；\n]{0,6}(资料|书籍|课件|笔记本)")),
    ("禁止录制/截屏", re.compile(r"(禁止|不得)[^。；\n]{0,8}(截图|录屏|拍照|外传)")),
    ("要求独立作答", re.compile(r"(独立作答|不得交流|无人监考|全程监考)")),
]
ALLOWED_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("明确允许开卷", re.compile(r"(开卷考试|允许查阅|可查阅|允许携带资料)")),
    ("明确允许 AI 辅助", re.compile(r"(允许|可使用|支持)[^。；\n]{0,8}(AI|ai|人工智能|大模型|智能助手)")),
]

BANNER = """
合规声明（考试 AI 辅助模式）
  1. 仅当本场考试经教师/课程**明确允许**开卷且允许 AI 辅助时，方可启用本模式。
  2. 本模式对考试页面**只读**：不点击、不填写、不勾选、不提交、不改布局。
  3. 不绕过验证码/登录/监考/切屏检测；不修改倒计时；不伪造任何记录。
  4. AI 输出仅供参考，最终答案与提交由你本人负责；提交前请你亲自确认。
  5. 若页面或课程规定出现“禁止 AI/闭卷”等表述，本模式会**自动关闭**。
"""


@dataclass
class GuardDecision:
    enabled: bool = False
    reason: str = ""
    forbidden_hits: list[str] = field(default_factory=list)
    allowed_hits: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return not self.enabled


class ExamGuard:
    def __init__(self, cfg: Any, store: Any) -> None:
        self.cfg = cfg
        self.store = store
        self.enabled = False
        self.course_name = ""
        self.rule_text_snapshot = ""

    # ------------------------------------------------------------ 规则扫描
    def scan_rules(self, text: str) -> GuardDecision:
        blob = re.sub(r"\s+", " ", str(text or ""))
        decision = GuardDecision()
        for label, pat in FORBIDDEN_PATTERNS:
            if pat.search(blob):
                decision.forbidden_hits.append(label)
        for label, pat in ALLOWED_PATTERNS:
            if pat.search(blob):
                decision.allowed_hits.append(label)
        if decision.forbidden_hits:
            decision.enabled = False
            decision.reason = "检测到禁止性表述：" + "、".join(decision.forbidden_hits)
        elif decision.allowed_hits:
            decision.enabled = True
            decision.reason = "检测到允许性表述：" + "、".join(decision.allowed_hits)
        else:
            decision.enabled = False
            decision.reason = "规则文本中未出现“允许开卷/允许 AI”的明确表述，默认不启用"
        return decision

    # ------------------------------------------------------------ 启用闸门
    def ensure_enabled(self, page_rule_text: str = "", interactive: bool = True,
                       course_name: str = "") -> GuardDecision:
        """考试 AI 模式的唯一入口。任何一步不满足 → 返回 blocked。"""
        if not self.cfg.get("EXAM_MODE_ALLOWED"):
            decision = GuardDecision(False, "user_config.py 中 EXAM_MODE_ALLOWED 未设为 True（需你确认本场考试允许 AI）")
            self._audit("refused_enable", decision.reason)
            return decision
        config_rule = str(self.cfg.get("EXAM_RULE_TEXT") or "")
        combined = f"{config_rule}\n{page_rule_text}"
        decision = self.scan_rules(combined) if combined.strip() else GuardDecision(
            False, "未提供考试规定原文（请在 user_config.py 填写 EXAM_RULE_TEXT，或让程序读取考试页面说明）"
        )
        if decision.blocked:
            self._audit("auto_disable", decision.reason)
            self.enabled = False
            return decision
        # 若配置与页面都没有读到明确许可：允许人工粘贴教师/课程通知原文（必须命中“允许”表述），
        # 并把这段许可原文写进审计，作为启用依据。仍然禁止任何绕过限制的用法。
        if decision.blocked and decision.reason.startswith(("规则文本中未出现", "未提供考试规定原文")) and interactive:
            from utils.console import ask

            print("未从配置 EXAM_RULE_TEXT 或考试页面读到“允许开卷 / 允许 AI”的明确表述。")
            grant = ask("请粘贴教师或课程通知中允许开卷并使用 AI 辅助的原文（直接回车则放弃启用考试 AI 模式）")
            if not grant.strip():
                self._audit("refused_enable", "无许可依据文本，考试 AI 模式未启用")
                return GuardDecision(False, "缺少允许开卷/允许 AI 的明确依据，考试 AI 模式未启用")
            decision = self.scan_rules(grant)
            if decision.blocked:
                self._audit("refused_enable", f"人工提供的文本未包含允许表述：{decision.reason}")
                err("你提供的内容中没有“允许开卷 / 允许使用 AI”的明确表述，考试 AI 模式不会启用。")
                return GuardDecision(False, "人工登记的许可依据不足")
            self.rule_text_snapshot = grant[:4000]
            self._audit("manual_grant", f"登记许可依据：{grant[:200]}")
            ok("已登记教师/课程许可依据（写入 exam_audit 表，可追溯）")
        phrase = str(self.cfg.get("EXAM_ACK_PHRASE") or "我已阅读并遵守考试规定")
        if interactive:
            print(BANNER)
            info(f"检测到的允许项：{'、'.join(decision.allowed_hits)}")
            if decision.forbidden_hits:
                warn(f"! 同时检测到禁止项：{'、'.join(decision.forbidden_hits)} —— 已自动关闭考试 AI 模式")
                self.enabled = False
                return GuardDecision(False, "存在禁止项，考试 AI 模式已自动关闭")
            from utils.console import ask

            course_name = course_name or ask("请输入本场考试所属课程名称").strip()
            typed = ask(f"请逐字输入“{phrase}”以确认你已阅读并遵守课程考试规定").strip()
            if typed != phrase:
                self._audit("refused_enable", "二次确认口令输入不一致")
                err("未通过二次确认，考试 AI 模式不会启用。")
                return GuardDecision(False, "二次确认失败")
            if not course_name:
                return GuardDecision(False, "必须填写课程名称，便于审计留痕")
        self.enabled = True
        self.course_name = course_name
        self.rule_text_snapshot = combined[:4000]
        decision.enabled = True
        decision.reason = (decision.reason or "") + "；已由用户本人二次确认"
        self._audit("enable", f"课程={course_name}；{decision.reason}")
        ok("考试 AI 辅助模式已启用（只读模式，不会改动考试页面）")
        return decision

    def runtime_check(self, rule_text: str) -> GuardDecision:
        """每次读取题目后复核：若中途出现禁止性表述，立刻自动关闭。"""
        decision = self.scan_rules(rule_text)
        if decision.forbidden_hits and self.enabled:
            self.enabled = False
            self._audit("auto_disable", "运行中检测到禁止项：" + "、".join(decision.forbidden_hits))
            warn("考试规则出现禁止性表述，考试 AI 模式已自动关闭，请按考试要求独立作答。")
        return decision

    # ------------------------------------------------------------ 练习/演示模式写闸门
    def is_real_exam_url(self, url: str) -> bool:
        """真实考试/学习通域名：无论练习模式开否，一律锁死写操作。"""
        host = (urlparse(str(url or "")).hostname or "").lower()
        if not host:
            return False
        for d in (self.cfg.get("REAL_EXAM_DOMAINS") or []):
            d = str(d).strip().lower().lstrip(".")
            if d and (host == d or host.endswith("." + d)):
                return True
        return False

    def allow_page_write(self, url: str, want_submit: bool = False) -> bool:
        """练习模式写闸门（域名 + “只锁考试”路径黑名单）。

        1) PRACTICE_MODE 必须开；自动提交还需 PRACTICE_ALLOW_SUBMIT；
        2) 不在 REAL_EXAM_DOMAINS 上的页面(本地/localhost/演示)：允许；
        3) 在真实学习通域名上：URL 命中 EXAM_URL_PATTERNS(考试/监考) → 一律只读；其余
           (视频小节/作业/练习) 允许自动作答。
        """
        if not self.cfg.get("PRACTICE_MODE"):
            return False
        if want_submit and not self.cfg.get("PRACTICE_ALLOW_SUBMIT"):
            return False
        if self.is_real_exam_url(url):
            low = str(url or "").lower()
            exam_pats = [str(p).lower() for p in (self.cfg.get("EXAM_URL_PATTERNS") or [])]
            return not any(p in low for p in exam_pats)
        return True

    def require_page_write(self, url: str, action: str, want_submit: bool = False) -> None:
        """放行则写审计；不放行则 refuse（抛 PermissionError）。"""
        if not self.allow_page_write(url, want_submit):
            self.refuse(f"{action}@{url or '?'}")
        self._audit("practice_write_allow", f"{action} url={url}")

    # ------------------------------------------------------------ 拒绝任何写操作
    def refuse(self, action: str) -> None:
        self._audit("refused_write", f"尝试执行被禁止的操作：{action}")
        raise PermissionError(
            f"该操作被安全策略拒绝：{action}\n"
            "考试辅助模式对考试页面只读，不允许填写/勾选/提交/修改倒计时/绕过限制。"
        )

    def _audit(self, event: str, detail: str = "") -> None:
        log.info("[考试模式审计] %s ｜ %s", event, detail)
        if self.store is not None:
            try:
                self.store.log_exam_event(event, detail)
            except Exception:
                log.debug("审计写入失败", exc_info=True)