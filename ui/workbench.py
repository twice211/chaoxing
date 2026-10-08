# -*- coding: utf-8 -*-
"""
ui.workbench —— 小窗主界面：小节工作台（左目录 + 右 视频/章节检测）

设计目标（对应你反馈的“顺序逻辑”问题）：
- 以“小节”为中心：左=章节目录，右=小节工作台（视频 / 章节检测 双标签，贴学习通心智）；
- 不再“一次点就卡死”：所有浏览器操作在同一线程，但播放改成“心跳步进”
  （Scheduler 每次只推进 Session 一小步，命令队列优先），你随时点暂停/停止/切标签都秒级响应；
- 合规不变：默认只读；自动作答/提交仅在 PRACTICE_MODE 且非真实考试域名时生效；
  真实考试域名（chaoxing/xuexitong）由 exam/guard 硬锁。
"""

from __future__ import annotations

import queue
from typing import Any

from ui import theme as T
from ui.scheduler import Scheduler
from utils.console import init_console
from utils.logger import get_logger
from utils.text import one_line

log = get_logger("ui.workbench")


class Workbench:
    """小节工作台界面：左目录 + 右(视频/章节检测)+ 底部状态条/输出。"""

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.out: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self.sched = Scheduler(cfg, self.out)
        self.root = None
        self.item_rows: list[int] = []

    def run(self) -> None:
        import tkinter as tk
        from tkinter import ttk
        from tkinter import scrolledtext

        init_console()
        root = tk.Tk()
        self.root = root
        T.apply(root)
        root.title("学习通助手 · 小节工作台")
        root.geometry("980x760+40+40")
        root.configure(bg=T.PAPER)
        root.attributes("-topmost", True)
        root.columnconfigure(0, weight=1)
        self.sched.start()

        # ---- 顶部：登录/课程/目录/进度/更多
        top = ttk.Frame(root, style="TFrame")
        top.grid(row=0, column=0, sticky="we", padx=T.SPACE["l"], pady=T.SPACE["s"])
        ttk.Button(top, text="登录", command=lambda: self.sched.submit("login")).pack(side="left")
        ttk.Button(top, text="读取课程", command=lambda: self.sched.submit("courses")).pack(side="left", padx=T.SPACE["s"])
        self.course_var = tk.StringVar(value="(选择课程)")
        self.course_box = ttk.Combobox(top, textvariable=self.course_var, state="readonly", width=22, values=[])
        self.course_box.pack(side="left")
        self.course_box.bind("<<ComboboxSelected>>", self._on_course_pick)
        ttk.Button(top, text="读取目录", command=lambda: self.sched.submit("catalog")).pack(side="left", padx=T.SPACE["s"])
        self.prog_var = tk.StringVar(value="进度 -/-")
        ttk.Label(top, textvariable=self.prog_var, style="MetricSmall.TLabel").pack(side="left", padx=T.SPACE["m"])
        ttk.Button(top, text="更多 ▾", command=self._open_more).pack(side="right")
        ttk.Button(top, text="⚙ 设置(AI)", command=self._open_settings).pack(side="right", padx=T.SPACE["s"])
        ttk.Button(top, text="✕ 取消", command=self._cancel_all).pack(side="right", padx=T.SPACE["s"])

        # ---- 主体：左目录 / 右工作台
        body = ttk.Panedwindow(root, orient="horizontal")
        body.grid(row=1, column=0, sticky="nsew", padx=T.SPACE["l"])
        root.rowconfigure(1, weight=1)

        left = ttk.Labelframe(body, text="章节目录", style="TFrame")
        body.add(left, weight=1)
        self.sect_list = tk.Listbox(left, activestyle="none", height=26, font=T.TYPE["label"],
                                    bg=T.PAPER_RAISED, fg=T.INK, relief="flat", highlightthickness=1,
                                    highlightbackground=T.RULE, borderwidth=0, selectborderwidth=0,
                                    selectbackground=T.INK, selectforeground="#FFFFFF")
        self.sect_list.pack(fill="both", expand=True, padx=T.SPACE["s"], pady=T.SPACE["s"])
        self.sect_list.bind("<<ListboxSelect>>", self._on_section_pick)

        right = ttk.Labelframe(body, text="小节工作台", style="TFrame")
        body.add(right, weight=2)

        # —— 当前小节：整屏唯一的大字 ——
        self.item_var = tk.StringVar(value="← 从左侧选择一个小节")
        hdr = ttk.Frame(right, style="Card.TFrame")
        hdr.pack(fill="x", padx=T.SPACE["s"], pady=(T.SPACE["s"], 0))
        ttk.Label(hdr, text="当前小节", style="Section.TLabel").pack(anchor="w", padx=T.SPACE["m"], pady=(T.SPACE["s"], 0))
        ttk.Label(hdr, textvariable=self.item_var, style="Display.TLabel").pack(anchor="w", padx=T.SPACE["m"],
                                                                                pady=(0, T.SPACE["s"]))

        nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True, padx=T.SPACE["s"], pady=T.SPACE["s"])
        vtab = ttk.Frame(nb, padding=(0, T.SPACE["xs"]))
        nb.add(vtab, text="视频 / 阅读")
        ttab = ttk.Frame(nb, padding=(0, T.SPACE["xs"]))
        nb.add(ttab, text="作业与练习")
        gtab = ttk.Frame(nb, padding=(0, T.SPACE["xs"]))
        nb.add(gtab, text="成绩")
        dtab = ttk.Frame(nb, padding=(0, T.SPACE["xs"]))
        nb.add(dtab, text="讨论")
        self.nb = nb

        # ============ 标签一：视频 / 阅读 ============
        pc = self._card(vtab, "视频播放")
        self.vstatus = tk.StringVar(value="未播放")
        ttk.Label(pc, textvariable=self.vstatus, style="MetricSmall.TLabel").pack(anchor="w")
        self.vbar = ttk.Progressbar(pc, mode="determinate")
        self.vbar.pack(fill="x", pady=T.SPACE["xs"])
        prow0 = ttk.Frame(pc, style="Card.TFrame")
        prow0.pack(fill="x", pady=T.SPACE["xs"])
        ttk.Button(prow0, text="播放本节", style="Primary.TButton", command=self._play).pack(side="left")
        ttk.Button(prow0, text="暂停", command=lambda: self.sched.submit("pause")).pack(side="left", padx=T.SPACE["s"])
        ttk.Button(prow0, text="继续播放", command=lambda: self.sched.submit("resume")).pack(side="left")
        ttk.Button(prow0, text="停止", command=lambda: self.sched.submit("stop_play")).pack(side="left", padx=T.SPACE["s"])
        self.next_var = tk.BooleanVar(value=False)
        self._check(pc, "完成后自动进入下一节", self.next_var,
                    lambda: self.sched.submit("auto_next", on=bool(self.next_var.get()))).pack(anchor="w", pady=T.SPACE["xs"])

        rc = self._card(vtab, "阅读 / 文档任务点")
        rrow = ttk.Frame(rc, style="Card.TFrame")
        rrow.pack(fill="x")
        self.read_min_var = tk.StringVar(value=str(int(self.cfg.get("READ_MIN_SECONDS") or 45)))
        ttk.Label(rrow, text="最低停留秒", style="Muted.TLabel").pack(side="left")
        ttk.Entry(rrow, textvariable=self.read_min_var, width=5).pack(side="left", padx=T.SPACE["s"])
        ttk.Button(rrow, text="阅读本节(计时)", style="Primary.TButton", command=self._read).pack(side="left")
        ttk.Button(rrow, text="停止", command=lambda: self.sched.submit("stop_play")).pack(side="left", padx=T.SPACE["s"])
        ttk.Label(rc, text="不填则按正文字数自动估时；真实停留后由平台标注才算完成。",
                  style="CardMuted.TLabel", wraplength=520, justify="left").pack(anchor="w")
        ttk.Label(vtab, text="提示：播放/阅读时请把“学习通”窗口留在前台，切走会被平台暂停（本工具不绕过）。",
                  style="Muted.TLabel", wraplength=560, justify="left").pack(anchor="w", padx=T.SPACE["m"])

        # ============ 标签二：作业与练习 ============
        ac = self._card(ttab, "本页作答")
        trow = ttk.Frame(ac, style="Card.TFrame")
        trow.pack(fill="x")
        ttk.Button(trow, text="进入检测", command=lambda: self.sched.submit("task_tab")).pack(side="left")
        ttk.Button(trow, text="解析本页", command=lambda: self.sched.submit("parse")).pack(side="left", padx=T.SPACE["s"])
        ttk.Button(trow, text="自动作答并提交", style="Primary.TButton",
                   command=lambda: self.sched.submit("auto", submit=bool(self.ps_var.get()))).pack(side="left")
        ttk.Button(trow, text="导出本页结构", command=lambda: self.sched.submit("dump")).pack(side="left", padx=T.SPACE["s"])
        self.pm_var = tk.BooleanVar(value=bool(self.cfg.get("PRACTICE_MODE")))
        self.ps_var = tk.BooleanVar(value=bool(self.cfg.get("PRACTICE_ALLOW_SUBMIT")))
        self.pf_var = tk.BooleanVar(value=bool(self.cfg.get("PRACTICE_FORCE_ANSWER")))
        self.pr_var = tk.BooleanVar(value=bool(self.cfg.get("PRACTICE_RETRY_ON_WRONG")))
        self.pskip_var = tk.BooleanVar(value=bool(self.cfg.get("PRACTICE_SKIP_ANSWERED")))
        def _pset():
            self.sched.submit("practice_set", mode=bool(self.pm_var.get()),
                              submit=bool(self.ps_var.get()), force=bool(self.pf_var.get()),
                              retry=bool(self.pr_var.get()), skip=bool(self.pskip_var.get()))
            self._practice_echo()

        cc = self._card(ttab, "逐节连做")
        crow2 = ttk.Frame(cc, style="Card.TFrame")
        crow2.pack(fill="x")
        self.chain_var = tk.StringVar(value="5")
        ttk.Label(crow2, text="连做节数", style="Muted.TLabel").pack(side="left")
        ttk.Entry(crow2, textvariable=self.chain_var, width=4).pack(side="left", padx=T.SPACE["s"])
        ttk.Button(crow2, text="▶ 开始连做", style="Primary.TButton",
                   command=self._chain_start).pack(side="left")
        ttk.Button(crow2, text="停止", command=self._stop_chain).pack(side="left", padx=T.SPACE["s"])
        ttk.Label(crow2, text="答完:", style="Muted.TLabel").pack(side="left", padx=(T.SPACE["m"], T.SPACE["xs"]))
        self.act_var = tk.StringVar(value="暂时保存" if str(self.cfg.get("PRACTICE_SUBMIT_ACTION") or "submit").lower() == "save" else "提交")
        _abox = ttk.Combobox(crow2, textvariable=self.act_var, state="readonly", width=8, values=("提交", "暂时保存"))
        _abox.pack(side="left")
        _abox.bind("<<ComboboxSelected>>", lambda _e: (self.sched.submit(
            "practice_set", mode=bool(self.pm_var.get()), submit=bool(self.ps_var.get()),
            force=bool(self.pf_var.get()), retry=bool(self.pr_var.get()),
            action=("save" if self.act_var.get() == "暂时保存" else "submit")),
            self._set_title_mode(),
            self._practice_echo(action=("save" if self.act_var.get() == "暂时保存" else "submit"))))
        self._set_title_mode()
        crow3 = ttk.Frame(cc, style="Card.TFrame")
        crow3.pack(fill="x", pady=T.SPACE["xs"])
        self.chain_skip_var = tk.BooleanVar(value=True)
        self._check(crow3, "跳过已完成", self.chain_skip_var).pack(side="left", padx=(0, T.SPACE["s"]))
        self.chain_restart_var = tk.BooleanVar(value=False)
        self._check(crow3, "从头重连", self.chain_restart_var).pack(side="left", padx=T.SPACE["s"])
        self.chain_sel_var = tk.BooleanVar(value=False)
        self._check(crow3, "从选中节开始", self.chain_sel_var).pack(side="left", padx=T.SPACE["s"])

        tc = self._card(ttab, "练习 / 演示开关")
        prow = ttk.Frame(tc, style="Card.TFrame")
        prow.pack(fill="x")
        self._check(prow, "练习/演示模式(自动作答)", self.pm_var, _pset).pack(side="left")
        self._check(prow, "允许自动提交", self.ps_var, _pset).pack(side="left", padx=T.SPACE["m"])
        self._check(prow, "AI 不确定也照答", self.pf_var, _pset).pack(side="left")
        prow2 = ttk.Frame(tc, style="Card.TFrame")
        prow2.pack(fill="x", pady=T.SPACE["xs"])
        self._check(prow2, "视频弹题答错自动重试", self.pr_var, _pset).pack(side="left")
        self._check(prow2, "跳过已作答(不重做/不取消已勾选)", self.pskip_var, _pset).pack(side="left", padx=T.SPACE["m"])
        ttk.Label(tc, text="自动作答仅在“练习/演示模式”且非真实考试域名生效；真实学习通考试页始终只读。勾选“跳过已作答”后，重跑只会补没做的题，不会改动/取消已保存的选择。",
                  style="CardMuted.TLabel", wraplength=600, justify="left").pack(anchor="w")

        # ============ 标签三：成绩（只读） ============
        gc = self._card(gtab, "课程成绩（只读）")
        grow = ttk.Frame(gc, style="Card.TFrame")
        grow.pack(fill="x")
        ttk.Button(grow, text="刷新成绩(只读抓取)", style="Primary.TButton",
                   command=lambda: self.sched.submit("grades", save=True)).pack(side="left")
        ttk.Button(grow, text="显示本地缓存",
                   command=lambda: self.sched.submit("grades", save=False)).pack(side="left", padx=T.SPACE["s"])
        ttk.Label(grow, text="· 仅读取，不改动学习通", style="Muted.TLabel").pack(side="left", padx=T.SPACE["s"])
        self.grade_text = scrolledtext.ScrolledText(gtab, height=22, wrap="word", font=T.TYPE["label"],
                                                    bg=T.PAPER_RAISED, fg=T.INK, relief="flat",
                                                    highlightthickness=1, highlightbackground=T.RULE, borderwidth=0)
        self.grade_text.pack(fill="both", expand=True, padx=T.SPACE["s"], pady=T.SPACE["s"])
        self.grade_text.configure(state="disabled")

        # ============ 标签四：讨论（“发表/回复”两个模块,按分值规则凑分） ============
        self.pd_var = tk.BooleanVar(value=bool(self.cfg.get("PRACTICE_ALLOW_DISCUSS_POST")))
        self.pds_var = tk.BooleanVar(value=bool(self.cfg.get("PRACTICE_ALLOW_DISCUSS_SUBMIT")))
        _dset = lambda: self.sched.submit("practice_set", mode=bool(self.pm_var.get()),
                                          submit=bool(self.ps_var.get()), force=bool(self.pf_var.get()),
                                          retry=bool(self.pr_var.get()), discuss=bool(self.pd_var.get()),
                                          discuss_submit=bool(self.pds_var.get()))
        gtc = self._card(dtab, "计分讨论 · 闸门与说明")
        gtrow = ttk.Frame(gtc, style="Card.TFrame")
        gtrow.pack(fill="x")
        self._check(gtrow, "允许自动发帖回复(默认关)", self.pd_var, _dset).pack(side="left")
        self._check(gtrow, "自动点发表/回复(默认关)", self.pds_var, _dset).pack(side="left", padx=T.SPACE["m"])
        ttk.Label(gtrow, text="每轮条数上限", style="Muted.TLabel").pack(side="left", padx=(T.SPACE["m"], T.SPACE["xs"]))
        self.discuss_max_var = tk.StringVar(value=str(int(self.cfg.get("DISCUSS_MAX") or 5)))
        def _dmax(_evt=None):
            try:
                self.sched.submit("discuss_set_max", value=int(self.discuss_max_var.get()))
            except (TypeError, ValueError):
                self.discuss_max_var.set(str(int(self.cfg.get("DISCUSS_MAX") or 5)))
        _dentry = ttk.Entry(gtrow, textvariable=self.discuss_max_var, width=4)
        _dentry.pack(side="left")
        _dentry.bind("<Return>", _dmax)
        _dentry.bind("<FocusOut>", _dmax)
        ttk.Button(gtrow, text="应用", command=_dmax).pack(side="left", padx=T.SPACE["xs"])
        self.discuss_rec_var = tk.StringVar(value="推荐 —")
        ttk.Label(gtrow, textvariable=self.discuss_rec_var, style="Muted.TLabel").pack(side="left", padx=T.SPACE["s"])
        self.discuss_steps = tk.StringVar(value="半自动：生成草稿 → 填入下一条 → 在浏览器发布 → 确认已发布。\n"
                                               "自动：开启自动提交 → 自动完成 → 核对本轮清单并确认；逐条核验，未确认成功即停。")
        ttk.Label(gtc, textvariable=self.discuss_steps, style="CardMuted.TLabel",
                  wraplength=640, justify="left").pack(anchor="w")

        def _dbox(parent, height):
            t = scrolledtext.ScrolledText(parent, height=height, width=34, wrap="word", font=T.TYPE["label"],
                                          bg=T.PAPER_RAISED, fg=T.INK, relief="flat",
                                          highlightthickness=1, highlightbackground=T.RULE, borderwidth=0)
            t.pack(fill="both", expand=True, pady=T.SPACE["xs"])
            t.configure(state="disabled")
            return t

        ds = ttk.Frame(dtab, style="TFrame")
        ds.pack(fill="both", expand=True, padx=2)

        pc = self._card(ds, "模块一 · 发表（课程提问）", fill="both")
        prow = ttk.Frame(pc, style="Card.TFrame")
        prow.pack(fill="x")
        ttk.Button(prow, text="生成草稿", style="Primary.TButton",
                   command=lambda: self.sched.submit("discuss_preview", kind="new_post")).pack(side="left")
        ttk.Button(prow, text="▶ 自动完成",
                   command=lambda: self.sched.submit("discuss_auto", kind="new_post")).pack(side="left", padx=T.SPACE["s"])
        ttk.Button(prow, text="填入下一条",
                   command=lambda: self.sched.submit("discuss_fill_next", kind="new_post")).pack(side="left")
        pconfirm = ttk.Frame(pc, style="Card.TFrame")
        pconfirm.pack(fill="x", pady=(T.SPACE["xs"], 0))
        ttk.Button(pconfirm, text="确认已发布",
                   command=lambda: self.sched.submit("discuss_confirm", kind="new_post")).pack(side="left", padx=T.SPACE["s"])
        ttk.Button(pconfirm, text="放弃草稿",
                   command=lambda: self.sched.submit("discuss_discard", kind="new_post")).pack(side="left")
        ttk.Button(pconfirm, text="确认未发布",
                   command=lambda: self._confirm_not_published("new_post")).pack(side="left", padx=T.SPACE["s"])
        self.discuss_post_text = _dbox(pc, 7)

        rc = self._card(ds, "模块二 · 回复（约150字）", fill="both")
        rrow = ttk.Frame(rc, style="Card.TFrame")
        rrow.pack(fill="x")
        ttk.Button(rrow, text="生成草稿", style="Primary.TButton",
                   command=lambda: self.sched.submit("discuss_preview", kind="reply")).pack(side="left")
        ttk.Button(rrow, text="▶ 自动完成",
                   command=lambda: self.sched.submit("discuss_auto", kind="reply")).pack(side="left", padx=T.SPACE["s"])
        ttk.Button(rrow, text="填入下一条",
                   command=lambda: self.sched.submit("discuss_fill_next", kind="reply")).pack(side="left")
        rconfirm = ttk.Frame(rc, style="Card.TFrame")
        rconfirm.pack(fill="x", pady=(T.SPACE["xs"], 0))
        ttk.Button(rconfirm, text="确认已发布",
                   command=lambda: self.sched.submit("discuss_confirm", kind="reply")).pack(side="left", padx=T.SPACE["s"])
        ttk.Button(rconfirm, text="放弃草稿",
                   command=lambda: self.sched.submit("discuss_discard", kind="reply")).pack(side="left")
        ttk.Button(rconfirm, text="确认未发布",
                   command=lambda: self._confirm_not_published("reply")).pack(side="left", padx=T.SPACE["s"])
        self.discuss_reply_text = _dbox(rc, 7)

        # ---- 底部：状态条 + 输出流（学习日志）
        self.status_var = tk.StringVar(value="就绪")
        sbar = ttk.Frame(root, style="TFrame")
        sbar.grid(row=2, column=0, sticky="we", padx=T.SPACE["l"], pady=(T.SPACE["s"], T.SPACE["xs"]))
        ttk.Label(sbar, text="学习日志", style="SectionPaper.TLabel").pack(side="left")
        tk.Frame(sbar, height=1, bg=T.RULE).pack(side="left", fill="x", expand=True, padx=T.SPACE["s"])
        ttk.Label(sbar, textvariable=self.status_var, style="Muted.TLabel").pack(side="right")

        self.text = scrolledtext.ScrolledText(root, wrap="word", bg=T.PAPER_RAISED, fg="#2C3A4B",
                                              relief="flat", highlightthickness=1, highlightbackground=T.RULE,
                                              font=T.TYPE["body"], spacing3=T.LINE_SPACING)
        self.text.grid(row=3, column=0, sticky="nsew", padx=T.SPACE["l"], pady=T.SPACE["s"])
        root.rowconfigure(3, weight=2)
        self.text.configure(state="disabled")
        for k, c in T.STATE.items():
            self.text.tag_configure(f"lv{k}", foreground=c)
        self.text.tag_configure("lvraw", foreground="#2C3A4B")

        ttk.Label(root, text="默认只读；仅开启“练习/演示模式”且目标非真实考试页时才自动作答/提交。作业、随堂题与考试计入成绩，请遵守课程规定。",
                  style="Muted.TLabel", wraplength=940, justify="left").grid(row=4, column=0, sticky="w",
                                                                             padx=T.SPACE["l"], pady=(0, T.SPACE["m"]))
        root.protocol("WM_DELETE_WINDOW", self._quit)
        self._load_courses()
        root.after(120, self._pump)
        root.mainloop()

    def _check(self, parent, text, var, command=None):
        import tkinter as tk
        return tk.Checkbutton(parent, text=text, variable=var, command=command,
                              bg=T.PAPER_RAISED, fg=T.INK, activebackground=T.PAPER_RAISED, activeforeground=T.INK,
                              selectcolor=T.PAPER, font=T.TYPE["label"], bd=0, highlightthickness=0, anchor="w")

    def _card(self, parent, title: str = "", fill: str = "x"):
        """一个“作业本卡片”:白底、细边、可选小标题;返回内部容器放控件。"""
        from tkinter import ttk
        outer = ttk.Frame(parent, style="Card.TFrame")
        outer.pack(fill=fill, expand=(fill != "x"), padx=T.SPACE["s"], pady=T.SPACE["s"],
                   side="left" if fill == "both" else "top")
        if title:
            ttk.Label(outer, text=title, style="Section.TLabel").pack(anchor="w", padx=T.SPACE["m"],
                                                                      pady=(T.SPACE["s"], 0))
        inner = ttk.Frame(outer, style="Card.TFrame")
        inner.pack(fill="both", expand=True, padx=T.SPACE["m"], pady=T.SPACE["s"])
        return inner

    def _load_courses(self) -> None:
        try:
            from database.store import Store
            s = Store(self.cfg.db_path)
            rows = s.list_courses()
            s.close()
            self._course_map = {c["name"]: int(c["id"]) for c in rows}
            self.course_box.configure(values=list(self._course_map.keys()))
        except Exception:
            self._course_map = {}

    def _on_course_pick(self, _e=None) -> None:
        cid = getattr(self, "_course_map", {}).get(self.course_var.get())
        if cid is not None:
            self.sched.submit("select_course", course_id=cid)

    def _on_section_pick(self, _e=None) -> None:
        sel = self.sect_list.curselection()
        if not sel:
            return
        line = self.sect_list.get(sel[0])
        try:
            item_id = int(line.split("|", 1)[0])
        except ValueError:
            return
        self.sched.submit("open_item", item_id=item_id)
        self.nb.select(0)

    def _set_title_mode(self) -> None:
        """标题栏常驻显示当前「答完」动作,一眼确认模式。"""
        try:
            self.root.title(f"学习通助手 · 小节工作台   [答完:{self.act_var.get()}]")
        except Exception:
            pass

    def _chain_start(self) -> None:
        try:
            limit = int(self.chain_var.get() or 5)
        except (ValueError, AttributeError):
            limit = 5
        start_id = None
        if self.chain_sel_var.get():
            sel = self.sect_list.curselection()
            if sel:
                try:
                    start_id = int(self.sect_list.get(sel[0]).split("|", 1)[0])
                except (ValueError, IndexError):
                    start_id = None
            if start_id is None:
                self._append("warn", "请先在左侧目录点选一个小节，再点连做（或取消勾选「从选中节开始」按进度续跑）。")
                return
        self.sched.submit("auto_chain", limit=limit, skip_done=bool(self.chain_skip_var.get()),
                          restart=bool(self.chain_restart_var.get()), start_id=start_id)

    def _stop_chain(self) -> None:
        """只取消当前长任务(连做/自动作答),调度线程保持存活,之后按钮仍可点。"""
        try:
            if self.sched.task is None:
                self.status_var.set("没有正在进行的作答任务。")
                return
            self.sched.stop_task()
            self.status_var.set("正在停止：当前这一步处理完即中止…")
        except Exception:
            pass

    def _play(self) -> None:
        sel = self.sect_list.curselection()
        if not sel:
            self._append("warn", "先从左侧目录选一个小节。")
            return
        try:
            item_id = int(self.sect_list.get(sel[0]).split("|", 1)[0])
        except ValueError:
            return
        self.sched.submit("play", item_id=item_id)

    def _cancel_all(self) -> None:
        """顶部常驻“取消”：立刻打断长任务/登录等待+排队停止播放器/会话。任何状态可用。"""
        try:
            self.sched.task_stop.set()          # UI 线程直接置位，最快打断协作式任务
            self.sched.login_cancel.set()       # 登录等待占死调度线程时队列里的 cancel 跑不到，必须直接置位打断
            self.sched.submit("cancel")          # 浏览器线程上停播放器/清会话
            self.status_var.set("已取消：停止当前任务/登录等待/播放…")
        except Exception:
            pass

    def _read(self) -> None:
        sel = self.sect_list.curselection()
        if not sel:
            self._append("warn", "先从左侧目录选一个阅读/文档小节。")
            return
        try:
            item_id = int(self.sect_list.get(sel[0]).split("|", 1)[0])
        except ValueError:
            return
        try:
            mn = int(float(self.read_min_var.get() or 0))
        except (ValueError, AttributeError):
            mn = 0
        self.sched.submit("read", item_id=item_id, min_seconds=(mn or None))

    def _cancel_login(self) -> None:
        """「取消登录」：正在等待登录时置位打断(do_login 取消分支会 logout);空闲时排队立即退出登录。"""
        self.sched.login_cancel.set()
        if not self.sched.login_pending.is_set():
            self.sched.submit("logout")

    def _open_settings(self) -> None:
        from ui.settings_dialog import open_settings
        open_settings(self.root, self.cfg, on_saved=self._after_settings,
                      on_cancel_login=self._cancel_login)

    def _after_settings(self, values: dict) -> None:
        self._append("ok", "设置已保存并生效(写入 user_config.py)。")
        try:
            self.pm_var.set(bool(self.cfg.get("PRACTICE_MODE")))
            self.ps_var.set(bool(self.cfg.get("PRACTICE_ALLOW_SUBMIT")))
            self.pf_var.set(bool(self.cfg.get("PRACTICE_FORCE_ANSWER")))
            self.pr_var.set(bool(self.cfg.get("PRACTICE_RETRY_ON_WRONG")))
        except Exception:
            pass
        self.sched.submit("ai_check")

    def _open_more(self) -> None:
        from tkinter import ttk
        top = __import__("tkinter").Toplevel(self.root)
        top.title("更多功能")
        top.geometry("380x400")
        top.transient(self.root)
        top.configure(bg=T.PAPER)
        T.apply(top)
        def _pset2():
            self.sched.submit("practice_set", mode=bool(self.pm_var.get()),
                              submit=bool(self.ps_var.get()), force=bool(self.pf_var.get()),
                              retry=bool(self.pr_var.get()))
            self._practice_echo()
        self._check(top, "练习/演示模式(自动作答)", self.pm_var, _pset2).pack(anchor="w", padx=T.SPACE["m"], pady=T.SPACE["xs"])
        self._check(top, "允许自动提交", self.ps_var, _pset2).pack(anchor="w", padx=T.SPACE["m"])
        self._check(top, "AI 不确定也照答(有风险)", self.pf_var, _pset2).pack(anchor="w", padx=T.SPACE["m"], pady=T.SPACE["xs"])
        self._check(top, "答错自动重试(视频弹题)", self.pr_var, _pset2).pack(anchor="w", padx=T.SPACE["m"])
        brow = ttk.Frame(top, style="TFrame")
        brow.pack(fill="x", padx=T.SPACE["m"], pady=T.SPACE["s"])
        ttk.Button(brow, text="⚙ 设置窗口(AI 密钥/模型/全部配置)", command=self._open_settings).pack(anchor="w")
        ttk.Button(brow, text="检查 AI 连接", command=lambda: self.sched.submit("ai_check")).pack(anchor="w", pady=T.SPACE["xs"])
        ttk.Frame(top, height=T.SPACE["s"]).pack()
        ttk.Button(top, text="错题列表", command=lambda: self.sched.submit("wrong_list")).pack(anchor="w", padx=T.SPACE["m"], pady=T.SPACE["xs"])
        row = ttk.Frame(top, style="TFrame")
        row.pack(fill="x", padx=T.SPACE["m"], pady=T.SPACE["s"])
        self.search_var = __import__("tkinter").StringVar()
        ttk.Entry(row, textvariable=self.search_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="搜索资料", command=lambda: self.sched.submit("search", text=self.search_var.get())).pack(side="left", padx=T.SPACE["s"])
        ttk.Label(top, text="知识库建库 / 考试辅助 等重功能仍可回到旧面板或命令行（main.py kb / exam）。",
                  style="Muted.TLabel", wraplength=320, justify="left").pack(anchor="w", padx=T.SPACE["m"], pady=T.SPACE["s"])

    def _append(self, level: str, text: str) -> None:
        tag = f"lv{level if level in T.STATE or level in ('raw', 'hint') else 'raw'}"
        self.text.configure(state="normal")
        self.text.insert("end", (text or "").rstrip() + "\n", (tag,))
        self.text.see("end")
        self.text.configure(state="disabled")

    def _practice_echo(self, action: str | None = None) -> None:
        """拨一下练习开关就即时回一行（不等调度线程），和“自动下一节：开”一样醒目。"""
        try:
            act = (action or str(self.cfg.get("PRACTICE_SUBMIT_ACTION") or "submit")).lower()
            self._append("info", "练习开关："
                                 f"自动作答={'开' if self.pm_var.get() else '关'}、"
                                 f"自动提交={'开' if self.ps_var.get() else '关'}、"
                                 f"AI不确定也答={'开' if self.pf_var.get() else '关'}、"
                                 f"答错重试={'开' if self.pr_var.get() else '关'}、"
                                 f"答完动作={'提交交卷' if act != 'save' else '暂时保存'}")
        except Exception:
            pass


    def _set_grade_text(self, text: str) -> None:
        self.grade_text.configure(state="normal")
        self.grade_text.delete("1.0", "end")
        self.grade_text.insert("end", (text or "").rstrip() + "\n")
        self.grade_text.configure(state="disabled")

    def _set_discuss_text(self, which: str, text: str) -> None:
        widget = self.discuss_post_text if which == "post" else self.discuss_reply_text
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", (text or "").rstrip() + "\n")
        widget.configure(state="disabled")

    def _confirm_discuss_batch(self, request: dict) -> None:
        """在 Tk 主线程展示本轮完整草稿；浏览器线程收到明确确认后才开始。"""
        import tkinter as tk
        from tkinter import scrolledtext, ttk
        dialog = tk.Toplevel(self.root)
        dialog.title("确认本轮讨论发布")
        dialog.geometry("680x520")
        dialog.transient(self.root)
        text = scrolledtext.ScrolledText(dialog, wrap="word", font=T.TYPE["label"])
        text.pack(fill="both", expand=True, padx=12, pady=12)
        text.insert("1.0", request["text"])
        text.configure(state="disabled")
        row = ttk.Frame(dialog)
        row.pack(fill="x", padx=12, pady=(0, 12))
        def finish(approved: bool) -> None:
            self.sched.submit("discuss_auto_confirm", token=request["token"], approved=approved)
            dialog.destroy()
        ttk.Button(row, text="确认发布本轮", command=lambda: finish(True)).pack(side="right")
        ttk.Button(row, text="取消", command=lambda: finish(False)).pack(side="right", padx=8)
        dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))

    def _confirm_not_published(self, kind: str) -> None:
        from tkinter import messagebox
        if messagebox.askyesno("核对发布结果", "已检查目标话题，确认该条确实未发布？\n确认后可以重新填入或提交。", parent=self.root):
            self.sched.submit("discuss_not_published", kind=kind)

    def _pump(self) -> None:
        try:
            while True:
                level, payload = self.out.get_nowait()
                if level == "courses":
                    self._load_courses()
                    continue
                if level == "course":
                    _cid, _name = payload.split("|", 1)
                    self.course_var.set(_name)
                    continue
                if level == "progress":
                    self.prog_var.set(f"进度 {payload}")
                    continue
                if level == "sections":
                    self.sect_list.delete(0, "end")
                    for idx, line in enumerate(payload.splitlines()):
                        self.sect_list.insert("end", line)
                        self._color_row(idx, line)
                    continue
                if level == "item":
                    parts = payload.split("|")
                    if len(parts) >= 3:
                        kind, pct = parts[0], parts[-1]
                        title = "|".join(parts[1:-1])
                        self.item_var.set(f"{title}   [{kind}]   {pct}")
                        self.vstatus.set("已选中，点“播放本节”")
                    continue
                if level == "row":
                    self._refresh_row_line(payload)
                    continue
                if level == "playstatus":
                    self.vstatus.set(payload)
                    self.status_var.set("运行中：" + payload)
                    parts = payload.split("｜")
                    if len(parts) >= 3:
                        try:
                            self.vbar["value"] = float(parts[2].strip().rstrip("%"))
                        except ValueError:
                            pass
                    continue
                if level == "grades":
                    self._set_grade_text(payload)
                    self.nb.select(2)
                    continue
                if level == "discuss_rec":
                    self.discuss_rec_var.set(str(payload))
                    continue
                if level == "discuss_confirm":
                    self._confirm_discuss_batch(payload)
                    continue
                if level in ("discuss_post", "discuss_reply"):
                    self._set_discuss_text("post" if level == "discuss_post" else "reply", payload)
                    self.nb.select(3)
                    continue
                if level == "focus":
                    continue
                if level == "alert":
                    self.status_var.set("⚠ " + payload)
                    self._append("warn", payload)
                    continue
                self._append(level, payload)
        except queue.Empty:
            pass
        self.root.after(120, self._pump)

    def _refresh_row_line(self, payload: str) -> None:
        from database.store import Store
        try:
            iid = int(payload.split("|", 1)[0])
            s = Store(self.cfg.db_path)
            row = s.q1("SELECT done,progress,kind,title FROM items WHERE id=?", (iid,))
            s.close()
        except Exception:
            return
        if not row:
            return
        mark = "✓" if row["done"] else ("▶" if (row["progress"] or 0) > 0 else "·")
        line = f"{iid}|{mark}|{row['kind']}|{one_line(row['title'])[:28]}"
        for idx in range(self.sect_list.size()):
            if self.sect_list.get(idx).split("|", 1)[0] == str(iid):
                self.sect_list.delete(idx)
                self.sect_list.insert(idx, line)
                self._color_row(idx, line)
                break

    def _color_row(self, idx: int, line: str) -> None:
        """目录行按状态取色：✓苔绿(完成)、▶墨蓝(进行中)、·石墨(未开始)。"""
        try:
            parts = line.split("|")
            mark = parts[1] if len(parts) > 1 else ""
            color = T.MOSS if mark == "✓" else (T.INK if mark == "▶" else T.INK_SOFT)
            self.sect_list.itemconfig(idx, fg=color)
        except Exception:
            pass

    def _quit(self) -> None:
        try:
            self.sched.shutdown()
        except Exception:
            log.debug("关闭调度器异常", exc_info=True)
        self.root.destroy()


def run_workbench(cfg: Any) -> int:
    try:
        import tkinter  # noqa: F401
    except Exception:
        from utils.console import warn
        warn("没有 tkinter，改用命令行：python main.py menu")
        return 2
    Workbench(cfg).run()
    return 0
