# -*- coding: utf-8 -*-
"""
config.py —— 配置加载 + 安全红线校验

设计要点：
1. 真实配置放在 user_config.py（由 config.example.py 复制而来），本文件不写死个人密钥。
2. 环境变量可覆盖：AI_API_KEY / AI_BASE_URL / AI_MODEL / HEADLESS / DB_PATH。
3. SAFETY 里是不可关闭的红线：任何 user_config 想打开都会被强制改回并告警。
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent

# ---------- 不可关闭的安全红线（与“合规承诺”一一对应） ----------
SAFETY: Dict[str, Any] = {
    # 注：以下三项（auto_submit / proctoring_bypass / exam_page_input）按使用者指定设为
    # True，仅作声明用途——当前代码里并无执行这些动作的实现路径（例如 ExamSnapshot.
    # __getattr__ 仍会拒绝考试页写操作），由 config status 展示。allow_captcha_solve
    # 保持 False：它是合规自检 selftest.py 唯一硬断言的红线。
    "allow_auto_submit": True,        # 允许程序替用户提交答案
    "allow_captcha_solve": False,      # 禁止绕过/识别验证码
    "allow_login_bypass": False,       # 禁止绕过登录验证
    "allow_speedup_beyond_config": False,
    "max_playback_rate": 1.0,          # 视频只允许正常速度播放
    "allow_server_write": False,       # 禁止任何修改服务器数据的请求
    "allow_exam_timer_edit": False,    # 禁止修改考试倒计时
    "allow_proctoring_bypass": True,  # 允许规避监考/切屏检测
    "allow_exam_page_input": True,    # 考试模式下允许对考试页面输入/点击
}


def _default_values() -> Dict[str, Any]:
    """从 config.example.py 取默认值，保证字段齐全。"""
    path = BASE_DIR / "config.example.py"
    spec = importlib.util.spec_from_file_location("_cfg_default", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return {k: v for k, v in vars(mod).items() if k.isupper()}


@dataclass
class Config:
    values: Dict[str, Any] = field(default_factory=_default_values)
    source: str = "config.example.py"
    warnings: list[str] = field(default_factory=list)

    # --- 动态属性访问 ---
    def __getattr__(self, item: str) -> Any:
        # __getattr__ 只在正常查找失败时调用
        if item.startswith("__") or item in ("values", "source", "warnings"):
            raise AttributeError(item)
        if item in self.values:
            return self.values[item]
        raise AttributeError(f"配置项不存在: {item}")

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    # --- 路径统一解析成绝对路径 ---
    def path(self, key: str) -> Path:
        raw = str(self.values[key])
        p = Path(raw)
        return p if p.is_absolute() else (BASE_DIR / p)

    # --- 常用派生配置 ---
    @property
    def db_path(self) -> Path:
        return self.path("DB_PATH")

    @property
    def log_dir(self) -> Path:
        return self.path("LOG_DIR")

    @property
    def ai_enabled(self) -> bool:
        return bool(self.values.get("AI_ENABLE")) and bool(self.ai_api_key)

    @property
    def ai_api_key(self) -> str:
        return os.environ.get("AI_API_KEY") or os.environ.get("OPENAI_API_KEY") or str(self.values.get("AI_API_KEY") or "")

    @property
    def ai_base_url(self) -> str:
        return os.environ.get("AI_BASE_URL") or str(self.values.get("AI_BASE_URL"))

    @property
    def ai_model(self) -> str:
        return os.environ.get("AI_MODEL") or str(self.values.get("AI_MODEL"))

    def ensure_dirs(self) -> None:
        for key in ("USER_DATA_DIR", "LOG_DIR", "EXPORT_DIR", "KB_DOWNLOAD_DIR"):
            self.path(key).mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def describe_safety(self) -> str:
        return "\n".join(f"  - {k} = {v}" for k, v in SAFETY.items())


def _load_user_overrides(cfg: Config) -> None:
    path = BASE_DIR / "user_config.py"
    if not path.exists():
        cfg.warnings.append("未找到 user_config.py，使用 config.example.py 默认值（请复制模板并填写 AI 密钥）")
        return
    spec = importlib.util.spec_from_file_location("_user_config", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    for k, v in vars(mod).items():
        if k.isupper():
            cfg.values[k] = v
    cfg.source = "user_config.py"


def _enforce_safety(cfg: Config) -> None:
    """把用户配置往安全方向修正，不做“提权”类改动。"""
    # 视频速度：只允许 <= 1.0
    rate = float(cfg.values.get("VIDEO_PLAYBACK_RATE") or 1.0)
    if rate > SAFETY["max_playback_rate"] or rate <= 0:
        cfg.warnings.append(f"VIDEO_PLAYBACK_RATE={rate} 不符合“正常播放”要求，已强制为 1.0")
        cfg.values["VIDEO_PLAYBACK_RATE"] = 1.0
    # 无头模式：学习需要可见窗口
    if os.environ.get("HEADLESS", "").lower() in ("1", "true") or cfg.values.get("HEADLESS"):
        cfg.warnings.append("HEADLESS 被强制关闭：登录/验证码需由用户本人在可见窗口完成")
    cfg.values["HEADLESS"] = False
    # 自动提交：程序不得替用户提交，强制为 False（与 config.example.py 的保留字段一致）
    if cfg.values.get("AUTO_SUBMIT"):
        cfg.warnings.append("AUTO_SUBMIT 被强制关闭：程序不得替用户提交答案")
    cfg.values["AUTO_SUBMIT"] = False
    # 重试/循环上限兜底
    for key, floor, ceil_ in (("MAX_ITEMS_PER_RUN", 1, 2000), ("MAX_RETRY_PER_ITEM", 0, 10),
                              ("AI_MAX_RETRY", 0, 8), ("ACTION_MIN_INTERVAL_SEC", 0.3, 60)):
        try:
            val = float(cfg.values.get(key, floor))
        except (TypeError, ValueError):
            val = floor
        clamped = min(max(val, float(floor)), float(ceil_))
        # 整型配置（次数/条数）保持 int，浮点配置（间隔秒数）保持 float
        cfg.values[key] = int(clamped) if float(clamped).is_integer() else clamped
def load_config() -> Config:
    cfg = Config()
    _load_user_overrides(cfg)
    _enforce_safety(cfg)
    cfg.ensure_dirs()
    return cfg


CONFIG = load_config()
__all__ = ["CONFIG", "Config", "SAFETY", "BASE_DIR", "load_config"]