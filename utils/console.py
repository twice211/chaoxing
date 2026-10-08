# -*- coding: utf-8 -*-
"""
utils.console —— 轻量终端输出（不引入第三方 UI 库）

约定：程序给用户看的“界面”一律走这里；调试信息走 logging。
"""

from __future__ import annotations
import os
import sys
import os
import sys
import textwrap

_COLOR = os.name != "nt" or bool(os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM"))
_console_ready = False


def init_console() -> None:
    """
    让中文与特殊符号在 Windows 控制台安全输出。

    1) 优先把控制台代码页切到 UTF-8（Windows 10+），避免中文乱码；
    2) 无论如何都把 errors 设为 replace —— 绝不允许因为某个符号无法编码而让程序崩溃。
    """
    global _console_ready
    if _console_ready:
        return
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            if os.name == "nt":
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                stream.reconfigure(errors="replace")
        except Exception:
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass
    _console_ready = True


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def banner(title: str, subtitle: str = "") -> None:
    line = "=" * 62
    print(f"\n{line}\n{_c('1;36', title)}")
    if subtitle:
        print(subtitle)
    print(line)


def section(title: str) -> None:
    print(f"\n{_c('1;35', '—— ' + title + ' ' + '—' * max(0, 56 - len(title) * 2))}")


def info(msg: str) -> None:
    print(f"{_c('36', '[信息]')} {msg}")


def ok(msg: str) -> None:
    print(f"{_c('1;32', '[完成]')} {msg}")


def warn(msg: str) -> None:
    print(f"{_c('1;33', '[注意]')} {msg}")


def err(msg: str) -> None:
    print(f"{_c('1;31', '[错误]')} {msg}", file=sys.stderr)


def step(msg: str) -> None:
    print(f"{_c('1;34', '[步骤]')} {msg}")


def kv(label: str, value: str, width: int = 12) -> None:
    print(f"{_c('90', label.ljust(width))} {value}")


def wrapped(msg: str, indent: str = "    ", width: int = 92) -> None:
    for line in textwrap.wrap(one_line_local(msg), width=width - len(indent)) or [""]:
        print(indent + line)


def one_line_local(msg: str) -> str:
    return " ".join(str(msg).split()) if "\n" not in str(msg) else str(msg)


def block(title: str, body: str, color: str = "37") -> None:
    """带标题的内容块，考试侧边栏终端版使用。"""
    print(f"\n{_c('1;' + color, '【' + title + '】')}")
    if body:
        for line in str(body).splitlines():
            print("  " + line)


def ask(text: str, default: str = "") -> str:
    try:
        suffix = f"（默认 {default}）" if default else ""
        val = input(f"{_c('1;33', '?')} {text}{suffix}: ").strip()
        return val or default
    except (EOFError, KeyboardInterrupt):
        print()
        return default


def confirm(text: str, default_no: bool = True) -> bool:
    """确认提示：回车 = 默认值（默认否）。取消时明确说明，避免被误认为卡住。"""
    hint = "（输入 y 确认，回车取消）" if default_no else "（回车确认，输入 n 取消）"
    val = ask(f"{text}{hint}", "").strip().lower()
    if not val:
        result = not default_no
    else:
        result = val in ("y", "yes", "1", "是", "对")
    if not result:
        info("已取消（未执行任何操作）。回到菜单/命令行，可重新发起。")
    return result


def pause(text: str = "按回车继续…") -> None:
    try:
        input(_c("90", text))
    except (EOFError, KeyboardInterrupt):
        print()