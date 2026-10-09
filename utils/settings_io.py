# -*- coding: utf-8 -*-
"""
utils.settings_io —— 小窗设置窗口的读写后端

做法：把可编辑项统一写进 user_config.py 末尾的“托管块”，
后面的定义会覆盖前面的，所以既保留你手写的注释与自定义内容，
又能让小窗改的配置真正落盘、下次启动仍然生效。
"""

from __future__ import annotations

import ast
import math
import os
import tempfile
from pathlib import Path
from typing import Any

MARK_START = "# >>> 小窗设置（由界面写入，可继续手动编辑）"
MARK_END = "# <<< 小窗设置结束"

# 允许小窗编辑的配置项：键 -> (类型, 中文标签, 分组)
EDITABLE: dict[str, tuple[str, str, str]] = {
    "AI_ENABLE": ("bool", "启用 AI 解析", "AI"),
    "AI_API_KEY": ("secret", "API 密钥", "AI"),
    "AI_BASE_URL": ("str", "接口地址 Base URL", "AI"),
    "AI_MODEL": ("str", "模型名", "AI"),
    "AI_TEMPERATURE": ("float", "温度（0-1，越小越稳）", "AI"),
    "AI_MAX_TOKENS": ("int", "单次最大输出 token", "AI"),
    "AI_TIMEOUT_SEC": ("int", "请求超时（秒）", "AI"),
    "AI_MAX_RETRY": ("int", "失败重试次数", "AI"),
    "POPUP_DETECT": ("bool", "随堂题自动探测并显示", "学习"),
    "POPUP_AI": ("bool", "随堂题调用 AI 解析", "学习"),
    "VIDEO_POLL_SEC": ("int", "播放进度轮询间隔（秒）", "学习"),
    "VIDEO_STALL_TIMEOUT_SEC": ("int", "播放停滞判定（秒）", "学习"),
    "READ_MIN_SECONDS": ("int", "阅读任务点：最低停留秒数（覆盖自动估算）", "学习"),
    "READ_MAX_SECONDS": ("int", "阅读任务点：停留上限秒数", "学习"),
    "READ_CHARS_PER_SEC": ("int", "阅读自动估时速度（字/秒，越大越短）", "学习"),
    "MAX_ITEMS_PER_RUN": ("int", "单次最多处理小节数", "学习"),
    "ACTION_MIN_INTERVAL_SEC": ("float", "页面操作最小间隔（秒）", "学习"),
    "PRACTICE_MODE": ("bool", "练习/演示模式：允许自动作答（仅非真实考试页）", "练习"),
    "PRACTICE_ALLOW_SUBMIT": ("bool", "练习模式：允许自动点击提交答案", "练习"),
    "PRACTICE_FORCE_ANSWER": ("bool", "练习模式：AI 需人工确认也照答(有错答风险)", "练习"),
    "PRACTICE_RETRY_ON_WRONG": ("bool", "练习模式：视频弹题答错自动换选项重试直到答对", "练习"),
    "PRACTICE_SUBMIT_ACTION": ("str", "答完后动作(submit=提交交卷 / save=暂时保存)", "练习"),
    "PRACTICE_SKIP_ANSWERED": ("bool", "自动作答跳过“已作答”的题(不重做/不取消已勾选)", "练习"),
    "PRACTICE_ALLOW_DISCUSS_POST": ("bool", "允许对“计分讨论”自动发帖/回复(默认关，风险：对外公开)", "练习"),
    "PRACTICE_ALLOW_DISCUSS_SUBMIT": ("bool", "讨论：填入后自动点“发表/回复”(默认关；关=只填、你亲自发布)", "练习"),
    "DISCUSS_MAX": ("int", "讨论：每轮最多处理条数(防刷屏；条数按讨论计分规则凑分规划)", "练习"),
    "DISCUSS_AI_PARALLEL": ("int", "讨论：AI 并行生成草稿路数(4≈快3倍；429限频调小)", "练习"),
    "KB_CHUNK_SIZE": ("int", "知识分块长度（字）", "知识库"),
    "KB_TOP_K": ("int", "搜索默认返回条数", "知识库"),
    "KB_MAX_DOC_MB": ("int", "单个资料大小上限（MB）", "知识库"),
    "EXAM_MODE_ALLOWED": ("bool", "允许启用考试辅助（须课程明确许可）", "考试"),
    "EXAM_RULE_TEXT": ("text", "考试规定原文（用于自动识别是否允许 AI）", "考试"),
    "EXAM_ACK_PHRASE": ("str", "二次确认口令", "考试"),
    "EXAM_ALLOW_SCROLL": ("bool", "允许“仅滚动翻页”", "考试"),
    "EXAM_AI_MAX_PER_MIN": ("int", "考试期间每分钟 AI 调用上限", "考试"),
    "EXAM_UI": ("str", "考试界面（tkinter / console）", "考试"),
    "LOG_LEVEL": ("str", "日志级别（INFO/DEBUG）", "其他"),
    "BROWSER_CDP_PORT": ("int", "浏览器本机调试端口(0=关；9222=允许本机工具协作读话题/填入)", "其他"),
}

GROUPS = ("AI", "学习", "练习", "知识库", "考试", "其他")


def config_path(base_dir: Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else Path(__file__).resolve().parents[1]
    return root / "user_config.py"


def load_current(cfg_obj: Any) -> dict[str, Any]:
    """取当前生效值（默认值 + 已写入的覆盖）。"""
    out: dict[str, Any] = {}
    for key in EDITABLE:
        try:
            out[key] = cfg_obj.get(key)
        except Exception:
            out[key] = None
    return out


def _literal(value: Any, kind: str) -> str:
    if kind == "bool":
        return "True" if value in (True, "True", "true", "1", 1) else "False"
    if kind == "int":
        try:
            return str(int(float(value)))
        except (TypeError, ValueError):
            return "0"
    if kind == "float":
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "0.0"
        if not math.isfinite(number):
            raise ValueError("浮点设置必须为有限数值")
        return repr(number)
    text = "" if value is None else str(value)
    return repr(text)


def save(values: dict[str, Any], path: Path | None = None) -> Path:
    """把托管块写入 user_config.py（不存在则创建）。"""
    target = Path(path) if path else config_path()
    text = target.read_text(encoding="utf-8-sig") if target.exists() else ""
    # 先删掉旧的托管块，避免重复堆积
    original_lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(original_lines) if line.rstrip("\r\n") == MARK_START]
    ends = [i for i, line in enumerate(original_lines) if line.rstrip("\r\n") == MARK_END]
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise ValueError("配置托管块标记不完整或重复，原文件已保留")
        text = "".join(original_lines[:starts[0]] + original_lines[ends[0] + 1:])
    lines = [MARK_START]
    for group in GROUPS:
        keys = [k for k, meta in EDITABLE.items() if meta[2] == group]
        if not keys:
            continue
        lines.append(f"\n# —— {group} ——")
        for key in keys:
            kind, label, _ = EDITABLE[key]
            value = values.get(key, None)
            lines.append(f"{key} = {_literal(value, kind)}    # {label}")
    lines.append("")
    lines.append(MARK_END)
    lines.append("")
    body = text.rstrip() + "\n" + "\n".join(lines)
    try:
        ast.parse(body)
        compile(body, str(target), "exec")
    except SyntaxError as exc:
        raise ValueError("配置语法检查失败，原文件已保留") from exc
    # Temporary credentials remain under the existing ignored local-data directory.
    private_dir = target.parent / "data" / ".config-write"
    private_dir.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         prefix="config-", suffix=".tmp", dir=private_dir,
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def reload_into(cfg_obj: Any, path: Path | None = None) -> int:
    """把落盘的覆盖重新读回内存配置对象，立即生效。"""
    target = Path(path) if path else config_path()
    if not target.exists():
        return 0
    namespace = {"__name__": "_user_cfg_reload", "__file__": str(target)}
    # Read current source directly; bytecode caches can be stale for same-size rapid saves.
    exec(compile(target.read_text(encoding="utf-8-sig"), str(target), "exec"), namespace)
    n = 0
    for k, v in namespace.items():
        if k.isupper():
            cfg_obj.values[k] = v
            n += 1
    return n


def mask(secret: str) -> str:
    s = str(secret or "")
    if len(s) <= 8:
        return "*" * len(s)
    return f"{s[:4]}…{s[-4:]}（{len(s)} 位）"
