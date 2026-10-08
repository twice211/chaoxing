# -*- coding: utf-8 -*-
"""
course.reading —— 阅读/文档任务点的“停留时长估算”(纯逻辑,可离线自检)

默认按正文长度估算停留秒数(字数 / 速度),并夹到 [最低, 上限];
用户在界面可把“最低停留秒数”调高,调高后至少停这么久(覆盖估算下限)。
只估算时长,不判断完成——完成与否由平台标注决定(不伪造)。
"""

from __future__ import annotations

from typing import Any


def estimate_read_seconds(text_len: int, min_seconds: Any = 45, max_seconds: Any = 240,
                          chars_per_sec: Any = 12) -> int:
    """返回建议停留秒数。缺字段/异常回退到 min_seconds。"""
    try:
        lo = max(1, int(float(min_seconds)))
    except (TypeError, ValueError):
        lo = 45
    try:
        hi = max(lo, int(float(max_seconds)))
    except (TypeError, ValueError):
        hi = max(lo, 240)
    try:
        cps = float(chars_per_sec)
    except (TypeError, ValueError):
        cps = 12.0
    if cps <= 0:
        cps = 12.0
    try:
        by_len = int(float(text_len) / cps)
    except (TypeError, ValueError):
        by_len = 0
    return min(hi, max(lo, by_len))
