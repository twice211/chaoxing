# -*- coding: utf-8 -*-
"""
utils.logger —— 统一日志

- 控制台：简洁带颜色
- 文件：logs/study_helper.log 轮转（5MB x 5）
- 所有模块统一通过 get_logger(__name__) 获取
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LEVEL_COLORS = {
    logging.DEBUG: "\033[90m",
    logging.INFO: "\033[36m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}
_RESET = "\033[0m"
_initialized = False


class _ColorFormatter(logging.Formatter):
    """控制台彩色格式化（非 TTY 时自动退回纯文本）。"""

    def __init__(self, fmt: str, use_color: bool) -> None:
        super().__init__(fmt)
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if self.use_color:
            color = _LEVEL_COLORS.get(record.levelno, "")
            if color:
                return f"{color}{text}{_RESET}"
        return text


def setup_logging(log_dir: str | Path = "logs", level: str = "INFO") -> logging.Logger:
    """初始化根日志（幂等，可重复调用）。"""
    global _initialized
    root = logging.getLogger("study_helper")
    if _initialized:
        return root
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    root.propagate = False

    fh = RotatingFileHandler(
        log_path / "study_helper.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"))
    root.addHandler(fh)

    ch = logging.StreamHandler(sys.stderr)
    use_color = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
    ch.setLevel(logging.WARNING)  # 普通 INFO 走 ui.console，避免刷屏；警告/错误直接可见
    ch.setFormatter(_ColorFormatter("%(levelname)s | %(message)s", use_color))
    root.addHandler(ch)

    _initialized = True
    return root


def get_logger(name: str = "study_helper") -> logging.Logger:
    """获取子日志器；根日志器未初始化时先做一次默认初始化。"""
    if not _initialized:
        setup_logging()
    if name == "study_helper" or name.startswith("study_helper."):
        return logging.getLogger(name)
    return logging.getLogger(f"study_helper.{name}")