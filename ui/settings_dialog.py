# -*- coding: utf-8 -*-
"""
ui.settings_dialog —— 小窗的“设置”窗口

按分组（AI / 学习 / 知识库 / 考试 / 其他）编辑配置，保存后：
  1) 写入 user_config.py 的托管块（下次启动仍然生效）；
  2) 立即回灌到内存配置，当前运行也生效，不用重启。
"""

from __future__ import annotations

from tkinter import messagebox
from typing import Any, Callable

from ui import theme as T
from utils import settings_io as S
from utils.logger import get_logger

log = get_logger("ui.settings_dialog")

WIDTHS = {"int": 8, "float": 8, "str": 46, "secret": 46, "text": 60}


class SettingsDialog:
    def __init__(self, parent: Any, cfg: Any, on_saved: Callable[[dict[str, Any]], None] | None = None,
                 on_cancel_login: Callable[[], None] | None = None) -> None:
        self.cfg = cfg
        self.on_saved = on_saved
        self.on_cancel_login = on_cancel_login
        self.vars: dict[str, Any] = {}
        self.top = __import__("tkinter").Toplevel(parent)
        self.top.title("设置")
        self.top.geometry("660x700+120+80")
        self.top.transient(parent)
        self.top.configure(bg=T.PAPER)
        self.top.attributes("-topmost", True)
        self._build()

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        style = T.apply(self.top)
        current = S.load_current(self.cfg)
        holder = ttk.Frame(self.top, style="TFrame")
        holder.pack(fill="both", expand=True, padx=T.SPACE["l"], pady=T.SPACE["s"])
        canvas = __import__("tkinter").Canvas(holder, highlightthickness=0, bg=T.PAPER, borderwidth=0)
        bar_v = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar_v.set)
        bar_v.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas, style="TFrame")
        win = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        self.top.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>") if e.widget is self.top else None)
        body.columnconfigure(0, weight=1)

        row = 0
        for group in S.GROUPS:
            keys = [k for k, meta in S.EDITABLE.items() if meta[2] == group]
            if not keys:
                continue
            box = ttk.LabelFrame(body, text=group, padding=T.SPACE["s"])
            box.grid(row=row, column=0, sticky="we", pady=(0, T.SPACE["s"]))
            box.columnconfigure(1, weight=1)
            row += 1
            for i, key in enumerate(keys):
                kind, label, _ = S.EDITABLE[key]
                value = current.get(key)
                ttk.Label(box, text=label, style="CardMuted.TLabel", width=22,
                          anchor="w").grid(row=i, column=0, sticky="w",
                                           padx=(0, T.SPACE["s"]), pady=2)
                if kind == "bool":
                    var = tk.BooleanVar(value=bool(value))
                    tk.Checkbutton(box, variable=var, bg=T.PAPER_RAISED, fg=T.INK,
                                   activebackground=T.PAPER_RAISED, activeforeground=T.INK,
                                   selectcolor=T.PAPER, font=T.TYPE["label"], bd=0,
                                   highlightthickness=0).grid(row=i, column=1, sticky="w")
                elif kind == "text":
                    txt = tk.Text(box, height=3, width=48, wrap="word", bg=T.PAPER_RAISED, fg=T.INK,
                                  insertbackground=T.INK, relief="flat", highlightthickness=1,
                                  highlightbackground=T.RULE, font=T.TYPE["label"])
                    txt.insert("1.0", "" if value is None else str(value))
                    txt.grid(row=i, column=1, sticky="we")
                    var = txt                              # type: ignore[assignment]
                elif kind == "secret":
                    frame = ttk.Frame(box, style="Card.TFrame")
                    frame.grid(row=i, column=1, sticky="we")
                    var = tk.StringVar(value="" if value is None else str(value))
                    show = tk.StringVar(value="*")
                    entry = ttk.Entry(frame, textvariable=var, show="*")
                    entry.pack(side="left", fill="x", expand=True)
                    tk.Checkbutton(frame, text="显示", variable=show, onvalue="", offvalue="*",
                                   command=lambda e=entry, s=show: e.configure(show=s.get()),
                                   bg=T.PAPER_RAISED, fg=T.INK_SOFT, activebackground=T.PAPER_RAISED,
                                   selectcolor=T.PAPER_RAISED, highlightthickness=0).pack(side="left",
                                                                                          padx=T.SPACE["s"])
                    if not var.get():
                        entry.insert(0, "")
                else:
                    var = tk.StringVar(value="" if value is None else str(value))
                    ttk.Entry(box, textvariable=var, width=WIDTHS.get(kind, 24)).grid(
                        row=i, column=1, sticky="w")
                self.vars[key] = var

        bar = ttk.Frame(self.top, style="TFrame")
        bar.pack(fill="x", padx=T.SPACE["l"], pady=T.SPACE["m"])
        ttk.Button(bar, text="保存并立即生效", style="Primary.TButton", command=self.save).pack(side="left")
        ttk.Button(bar, text="测试 AI 连接", command=self.test_ai).pack(side="left", padx=T.SPACE["s"])
        ttk.Button(bar, text="取消登录", command=self.cancel_login).pack(side="left")
        ttk.Button(bar, text="关闭", command=self.top.destroy).pack(side="right")
        self.status = tk.StringVar(value="修改后点“保存并立即生效”，会写入 user_config.py 并在当前运行中生效。")
        ttk.Label(self.top, textvariable=self.status, style="Muted.TLabel", wraplength=600,
                  justify="left").pack(fill="x", padx=T.SPACE["l"], pady=(0, T.SPACE["m"]))

    # ------------------------------------------------------------ 取值/保存
    def collect(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, var in self.vars.items():
            kind = S.EDITABLE[key][0]
            if kind == "bool":
                out[key] = bool(var.get())
            elif kind == "text":
                out[key] = var.get("1.0", "end").strip()          # type: ignore[attr-defined]
            elif kind == "int":
                try:
                    out[key] = int(float(str(var.get()).strip() or 0))
                except ValueError:
                    out[key] = 0
            elif kind == "float":
                try:
                    out[key] = float(str(var.get()).strip() or 0)
                except ValueError:
                    out[key] = 0.0
            else:
                out[key] = str(var.get()).strip()
        return out

    def save(self) -> None:
        values = self.collect()
        try:
            path = S.save(values)
            applied = S.reload_into(self.cfg, path)
            self.status.set(f"已写入 {path.name}（{applied} 项覆盖），当前运行立即生效。")
            if self.on_saved:
                self.on_saved(values)
        except Exception as exc:
            log.exception("保存设置失败")
            messagebox.showerror("保存失败", f"{exc}", parent=self.top)

    def cancel_login(self) -> None:
        if not self.on_cancel_login:
            messagebox.showinfo("取消登录", "当前没有可取消的登录流程。", parent=self.top)
            return
        try:
            self.on_cancel_login()
            self.status.set("已请求取消登录：停止等待你在浏览器中登录（浏览器窗口保持打开）。")
        except Exception as exc:
            log.exception("取消登录失败")
            messagebox.showerror("取消登录失败", f"{exc}", parent=self.top)

    def test_ai(self) -> None:
        import threading

        values = self.collect()
        self.status.set("正在测试 AI 连接…")

        def run() -> None:
            try:
                from ai.client import AIClient

                probe_cfg = self.cfg
                old_key = probe_cfg.values.get("AI_API_KEY")
                probe_cfg.values["AI_API_KEY"] = values.get("AI_API_KEY") or old_key
                probe_cfg.values["AI_BASE_URL"] = values.get("AI_BASE_URL") or probe_cfg.values.get("AI_BASE_URL")
                probe_cfg.values["AI_MODEL"] = values.get("AI_MODEL") or probe_cfg.values.get("AI_MODEL")
                probe_cfg.values["AI_ENABLE"] = True
                client = AIClient(probe_cfg)
                reply = client.ask("你是连通性测试助手", "只回复两个字：正常", purpose="settings_ping")
                self.top.after(0, lambda: self.status.set(f"AI 正常：模型 {probe_cfg.ai_model} 回复“{reply[:12]}”"))
            except Exception as exc:
                msg = str(exc)[:200]
                self.top.after(0, lambda: self.status.set(f"AI 测试失败：{msg}"))
                self.top.after(0, lambda: messagebox.showwarning("AI 测试失败", msg, parent=self.top))

        threading.Thread(target=run, daemon=True).start()


def open_settings(parent: Any, cfg: Any, on_saved: Callable[[dict[str, Any]], None] | None = None,
                  on_cancel_login: Callable[[], None] | None = None) -> Any:
    dlg = SettingsDialog(parent, cfg, on_saved, on_cancel_login)
    return dlg.top