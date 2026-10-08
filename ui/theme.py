# -*- coding: utf-8 -*-
"""
ui.theme —— 设计令牌（按 frontend-design 技能规范制定）

主题：作业本与批注。冷调纸面 + 墨蓝正文，朱砂只留给“需要你处理的那件事”，
苔绿表示已完成，石墨表示次要信息。数字与计时使用等宽字体以保证对齐。
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------- 色彩
PAPER = "#F2F4F7"        # 纸面（窗口底）
PAPER_RAISED = "#FFFFFF"  # 内容面（输出流、列表）
INK = "#1F3A63"          # 墨蓝：标题与正文主色
INK_SOFT = "#5A6472"     # 石墨：次要信息
CINNABAR = "#C2333D"     # 朱砂：弹题/错误（全窗口唯一高饱和强调）
MOSS = "#2E6B4F"         # 苔绿：完成/成功
RULE = "#D6DBE3"         # 分隔线
FOCUS = "#8FA6C4"        # 键盘焦点
STATE = {
    "ok": MOSS,
    "err": CINNABAR,
    "warn": "#8A5A00",
    "ai": INK,
    "info": "#2C3A4B",
    "item": INK_SOFT,
}

# ---------------------------------------------------------------- 字体
CJK = "Microsoft YaHei UI"
MONO = "Consolas"
TYPE = {
    "display": (CJK, 15, "bold"),     # 课程名（唯一的“大”）
    "step": (CJK, 10),                # 步骤轨
    "body": (CJK, 10),                # 输出流
    "label": (CJK, 9),                # 次要标签
    "metric": (MONO, 12, "bold"),     # 数字：进度、剩余
    "metric_small": (MONO, 9),        # 数字：秒数、百分比
    "button": (CJK, 10),
}

# ---------------------------------------------------------------- 间距（4 的倍数）
SPACE = {"xs": 4, "s": 8, "m": 12, "l": 16, "xl": 24}
LINE_SPACING = 4        # 输出流行距
MAX_COLS = 78           # 输出流行宽（字符），保证可读


def apply(root: Any) -> Any:
    """把令牌写进 ttk 样式，返回 style 对象。"""
    from tkinter import ttk

    style = ttk.Style(root)
    try:
        style.theme_use("clam")        # clam 便于自定义底色/边框
    except Exception:
        pass
    style.configure(".", background=PAPER, foreground=INK, font=TYPE["body"],
                    borderwidth=0, relief="flat", focuscolor=FOCUS,
                    highlightthickness=1, highlightbackground=RULE, highlightcolor=FOCUS)
    style.configure("TFrame", background=PAPER)
    style.configure("Card.TFrame", background=PAPER_RAISED, borderwidth=1, relief="solid",
                    bordercolor=RULE)
    style.configure("Alert.TFrame", background="#FBECEC", borderwidth=1, relief="solid",
                    bordercolor=CINNABAR)
    style.configure("Step.TFrame", background=PAPER)
    style.configure("TLabel", background=PAPER, foreground=INK, font=TYPE["body"])
    style.configure("Card.TLabel", background=PAPER_RAISED, foreground=INK, font=TYPE["body"])
    style.configure("Muted.TLabel", background=PAPER, foreground=INK_SOFT, font=TYPE["label"])
    style.configure("CardMuted.TLabel", background=PAPER_RAISED, foreground=INK_SOFT, font=TYPE["label"])
    # 分节小标题：石墨、字距略开、非全大写——给卡片一个安静的名字
    style.configure("Section.TLabel", background=PAPER_RAISED, foreground=INK_SOFT,
                    font=(CJK, 9, "bold"))
    style.configure("SectionPaper.TLabel", background=PAPER, foreground=INK_SOFT,
                    font=(CJK, 9, "bold"))
    style.configure("Display.TLabel", background=PAPER, foreground=INK, font=TYPE["display"])
    style.configure("Metric.TLabel", background=PAPER, foreground=INK, font=TYPE["metric"])
    style.configure("MetricSmall.TLabel", background=PAPER, foreground=INK_SOFT, font=TYPE["metric_small"])
    style.configure("Alert.TLabel", background="#FBECEC", foreground=CINNABAR, font=(CJK, 10, "bold"))
    style.configure("Step.TLabel", background=PAPER, foreground=INK_SOFT, font=TYPE["step"])
    style.configure("StepOn.TLabel", background=INK, foreground="#FFFFFF", font=(CJK, 10, "bold"))
    style.configure("StepDone.TLabel", background=PAPER, foreground=MOSS, font=TYPE["step"])
    style.configure("TButton", background=PAPER_RAISED, foreground=INK, font=TYPE["button"],
                    padding=(SPACE["m"], SPACE["s"]), bordercolor=RULE, lightcolor=PAPER_RAISED,
                    darkcolor=PAPER_RAISED, arrowcolor=INK)
    style.map("TButton",
              background=[("active", "#E7ECF3"), ("pressed", "#DCE3EC"), ("disabled", PAPER)],
              foreground=[("disabled", INK_SOFT)],
              bordercolor=[("active", INK_SOFT)])
    style.configure("Primary.TButton", background=INK, foreground="#FFFFFF", font=(CJK, 10, "bold"),
                    bordercolor=INK, lightcolor=INK, darkcolor=INK)
    style.map("Primary.TButton", background=[("active", "#16304F"), ("pressed", "#0F2740")])
    style.configure("Quiet.TButton", background=PAPER, foreground=INK_SOFT, bordercolor=PAPER,
                    lightcolor=PAPER, darkcolor=PAPER)
    style.map("Quiet.TButton", background=[("active", "#E7ECF3")])
    style.configure("TEntry", fieldbackground=PAPER_RAISED, foreground=INK, insertcolor=INK,
                    bordercolor=RULE, lightcolor=PAPER_RAISED, darkcolor=PAPER_RAISED, padding=6)
    style.map("TEntry", bordercolor=[("focus", INK)])
    style.configure("TNotebook", background=PAPER, borderwidth=0, tabmargins=(8, 6, 8, 0))
    style.configure("TNotebook.Tab", background=PAPER_RAISED, foreground=INK_SOFT, font=TYPE["button"],
                    padding=(SPACE["l"], SPACE["s"]), bordercolor=RULE)
    style.map("TNotebook.Tab",
              background=[("selected", INK)], foreground=[("selected", "#FFFFFF")],
              expand=[("selected", (0, 0, 0, 0))])
    style.configure("Treeview", background=PAPER_RAISED, fieldbackground=PAPER_RAISED,
                    foreground=INK, rowheight=26, bordercolor=RULE)
    # 步骤轨按钮：可点击，同时表达“已完成 / 当前 / 待办”
    style.configure("StepBtn.TButton", background=PAPER, foreground=INK_SOFT, font=(CJK, 10),
                    bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER, padding=(8, 4))
    style.map("StepBtn.TButton", background=[("active", "#E7ECF3")], foreground=[("active", INK)])
    style.configure("StepOn.TButton", background=INK, foreground="#FFFFFF", font=(CJK, 10, "bold"),
                    bordercolor=INK, lightcolor=INK, darkcolor=INK, padding=(8, 4))
    style.map("StepOn.TButton", background=[("active", "#16304F")], foreground=[("active", "#FFFFFF")])
    style.configure("StepDone.TButton", background=PAPER, foreground=MOSS, font=(CJK, 10),
                    bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER, padding=(8, 4))
    style.map("StepDone.TButton", background=[("active", "#E7F1EC")], foreground=[("active", MOSS)])
    style.configure("Vertical.TScrollbar", background=PAPER, troughcolor=PAPER, arrowcolor=INK_SOFT,
                    bordercolor=PAPER)
    style.configure("Horizontal.TProgressbar", background=MOSS, troughcolor="#E3E8EF",
                    bordercolor=RULE, lightcolor=MOSS, darkcolor=MOSS)
    return style