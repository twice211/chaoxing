# -*- coding: utf-8 -*-
"""
ui.sidebar —— 考试侧边栏（tkinter 小窗，置顶、只读展示）

特点：
- 独立窗口，浮在学习通旁边，**不注入、不改动学习通页面**；
- 界面区块：当前题目 / AI 分析 / 答案 / 课程依据 / 相关知识点 / 相似题 / 下一题；
- 顶部带合规提示与只读倒计时；
- 所有耗时操作在后台线程执行，结果通过 after() 回主线程刷新，界面不卡死。
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable

from utils.console import err, info, warn
from utils.logger import get_logger

log = get_logger("ui.sidebar")

SIDEBAR_CSS_TITLE = "学习通考试辅助侧边栏（只读 · 需考试明确允许 AI）"


def tkinter_available() -> bool:
    try:
        import tkinter  # noqa: F401

        return True
    except Exception:
        return False


class ExamSidebar:
    def __init__(self, assistant: Any, fallback_title: str = "") -> None:
        self.assistant = assistant
        self._q: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._running = False

    # ------------------------------------------------------------ 启动
    def run(self) -> None:
        import tkinter as tk
        from tkinter import scrolledtext, ttk

        root = tk.Tk()
        root.title(SIDEBAR_CSS_TITLE)
        root.geometry("640x780+60+60")
        root.attributes("-topmost", True)
        root.protocol("WM_DELETE_WINDOW", lambda: self._quit(root))

        top = ttk.Frame(root, padding=(8, 6))
        top.pack(fill="x")
        status = ttk.Label(top, text="状态：准备就绪（不会改动考试页面）", foreground="#0a6", wraplength=600)
        status.pack(side="left")

        bar = ttk.Frame(root, padding=(8, 4))
        bar.pack(fill="x")
        buttons = [
            ("读取当前题(A)", lambda: self._submit("读取中…", self.assistant.read_current)),
            ("整卷概览(O)", lambda: self._submit("识别整卷题目…", self.assistant.read_all)),
            ("AI 分析本题(D)", lambda: self._submit("AI 分析中…", self.assistant.analyze)),
            ("下一题·仅滚动(N)", lambda: self._submit("滚动页面…", lambda: self.assistant.next_question(1))),
            ("上一题(P)", lambda: self._submit("滚动页面…", lambda: self.assistant.next_question(-1))),
            ("刷新状态(K)", lambda: self._submit("刷新…", self.assistant.refresh_status)),
            ("提交前检查(T)", lambda: self._submit("生成清单…", self.assistant.pre_submit_report)),
        ]
        for text, cmd in buttons:
            ttk.Button(bar, text=text, command=cmd, width=16).pack(side="left", padx=3, pady=2)

        search_frame = ttk.Frame(root, padding=(8, 2))
        search_frame.pack(fill="x")
        ttk.Label(search_frame, text="快速搜索：").pack(side="left")
        entry = ttk.Entry(search_frame, width=48)
        entry.pack(side="left", fill="x", expand=True)
        ttk.Button(search_frame, text="搜资料/题目/错题(S)",
                   command=lambda: self._submit("检索本地资料…",
                                                lambda: self.assistant.search(entry.get())
                                                )).pack(side="left", padx=4)
        ttk.Button(search_frame, text="按题号分析",
                   command=lambda: self._submit("AI 分析中…",
                                                lambda: self.assistant.analyze_number(entry.get() or "1")
                                                )).pack(side="left", padx=4)
        ttk.Button(search_frame, text="退出(Q)", command=lambda: self._quit(root)).pack(side="right")

        body = scrolledtext.ScrolledText(root, wrap="word", font=("Microsoft YaHei UI", 10), height=40)
        body.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        body.insert("end", self.assistant.state.render())
        body.configure(state="disabled")

        entry.bind("<Return>", lambda e: self._submit("检索本地资料…", lambda: self.assistant.search(entry.get())))
        root.bind("<Escape>", lambda e: self._quit(root))
        self._running = True
        self._status = status
        self._body = body
        root.after(120, self._poll)
        root.mainloop()

    # ------------------------------------------------------------ 工具
    def _submit(self, tip: str, fn: Callable[[], str]) -> None:
        self._status.configure(text=f"状态：{tip}")
        self._running = True

        def _worker() -> None:
            try:
                text = fn()
                self._q.put(("body", text or "（无输出）"))
            except PermissionError as exc:
                self._q.put(("warn", str(exc)))
            except Exception as exc:
                log.exception("侧边栏任务失败")
                self._q.put(("err", f"操作失败：{exc}"))
            finally:
                self._q.put(("state", "就绪（不会改动考试页面）"))

        threading.Thread(target=_worker, daemon=True).start()

    def _poll(self) -> None:
        import tkinter as tk

        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "body":
                    self._body.configure(state="normal")
                    self._body.delete("1.0", "end")
                    self._body.insert("end", f"{payload}\n\n" + self.assistant.state.render())
                    self._body.configure(state="disabled")
                elif kind == "state":
                    self._status.configure(text=f"状态：{payload}", foreground="#0a6")
                elif kind == "warn":
                    self._status.configure(text=f"状态：{payload}", foreground="#c60")
                elif kind == "err":
                    self._status.configure(text=f"状态：{payload}", foreground="#c00")
        except queue.Empty:
            pass
        if self._running:
            self._after()

    def _after(self) -> None:
        import tkinter as tk

        try:
            self._body.after(150, self._poll)
        except Exception:
            pass

    def _quit(self, root: Any) -> None:
        self._running = False
        try:
            self.assistant.close()
        except Exception:
            log.debug("关闭助手异常", exc_info=True)
        root.destroy()
        info("侧边栏已关闭。考试提交请你自己在学习通页面完成。")


def run_gui_or_console(assistant: Any, prefer: str = "tkinter") -> None:
    """优先图形侧边栏；不可用时退回终端界面。"""
    if prefer == "tkinter" and tkinter_available():
        try:
            ExamSidebar(assistant).run()
            return
        except Exception as exc:
            warn(f"图形侧边栏启动失败（{exc}），退回终端模式")
    from exam.assistant import run_console_ui

    run_console_ui(assistant)