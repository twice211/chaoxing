"""Validated desktop API; browser operations stay on the scheduler thread."""
from __future__ import annotations

import math
import logging
import queue
import sqlite3
import threading
from contextlib import closing
from typing import Callable

from config import Config
from ui.scheduler import Scheduler
from utils import settings_io as settings
from utils.logger import get_logger
from utils.text import one_line

log = get_logger("ui.web_bridge")


class _RedactingFilter(logging.Filter):
    def __init__(self, redact: Callable[[object], object]) -> None:
        super().__init__()
        self._redact = redact

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = str(self._redact(logging.Formatter().formatException(record.exc_info)))
        return True


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("开关值必须为布尔值")
    return value


def _integer(value: object, low: int = 1, high: int = 2_147_483_647) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ValueError(f"整数必须在 {low} 到 {high} 之间")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or len(value) > 20_000:
        raise ValueError("文本必须为字符串，且不超过 20000 字")
    return value


def _kind(value: object) -> str:
    if value not in ("new_post", "reply"):
        raise ValueError("请选择发表或回复模块")
    return str(value)


def _optional_id(value: object) -> int | None:
    return None if value is None else _integer(value)


_REQUIRED = object()
_NO_PARAMS = ("login", "logout", "courses", "catalog", "pause", "resume", "stop_play",
              "cancel", "task_tab", "parse", "stop", "dump", "wrong_list", "ai_check", "ai_test")
_COMMANDS: dict[str, dict[str, tuple[Callable[[object], object], object]]] = {
    action: {} for action in _NO_PARAMS
}
_COMMANDS.update({
    "select_course": {"course_id": (_integer, _REQUIRED)},
    "open_item": {"item_id": (_integer, _REQUIRED)},
    "play": {"item_id": (_integer, _REQUIRED)},
    "auto_next": {"on": (_boolean, _REQUIRED)},
    "auto": {"submit": (_boolean, False)},
    "auto_chain": {"limit": (lambda v: _integer(v, 1, 2000), 5),
                   "skip_done": (_boolean, True), "restart": (_boolean, False),
                   "start_id": (_optional_id, None)},
    "grades": {"save": (_boolean, True)},
    "read": {"item_id": (_integer, _REQUIRED),
             "min_seconds": (lambda v: None if v is None else _integer(v, 0, 86_400), None)},
    "search": {"text": (_text, _REQUIRED)},
    "discuss_set_max": {"value": (lambda v: _integer(v, 1, 1000), _REQUIRED)},
    "discuss_auto_confirm": {"token": (_integer, _REQUIRED), "approved": (_boolean, False)},
})
for _action in ("discuss_preview", "discuss_fill_next", "discuss_auto", "discuss_confirm",
                "discuss_discard", "discuss_not_published"):
    _COMMANDS[_action] = {"kind": (_kind, "new_post")}

_RANGES = {
    "AI_TEMPERATURE": (0, 1), "AI_MAX_TOKENS": (1, 1_000_000),
    "AI_TIMEOUT_SEC": (1, 3600), "AI_MAX_RETRY": (0, 8),
    "DISCUSS_MAX": (1, 1000), "DISCUSS_AI_PARALLEL": (1, 64),
    "BROWSER_CDP_PORT": (0, 65535),
}


def _validate_settings(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict) or not all(isinstance(k, str) for k in raw):
        raise ValueError("设置必须为键值对象")
    result: dict[str, object] = {}
    for key, value in raw.items():
        if key not in settings.EDITABLE:
            raise ValueError("包含不支持的设置项")
        kind = settings.EDITABLE[key][0]
        if kind == "bool":
            result[key] = _boolean(value)
        elif kind in ("int", "float"):
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ValueError("数字设置格式无效")
            try:
                number = float(value)
            except ValueError as exc:
                raise ValueError("数字设置格式无效") from exc
            low, high = _RANGES.get(key, (0, 1_000_000))
            if not math.isfinite(number) or not low <= number <= high:
                raise ValueError(f"{settings.EDITABLE[key][1]}必须在 {low} 到 {high} 之间")
            if kind == "int" and not number.is_integer():
                raise ValueError("整数设置不能包含小数")
            result[key] = int(number) if kind == "int" else number
        else:
            result[key] = _text(value).strip()
    return result


class DesktopScheduler(Scheduler):
    """Desktop additions reuse the same browser-thread command loop."""

    _NEEDS_IDLE = Scheduler._NEEDS_IDLE | {"save_settings", "ai_test"}

    def run(self) -> None:
        try:
            super().run()
        finally:
            if self.browser is not None:
                try:
                    self.browser.stop()
                except Exception:
                    log.exception("关闭浏览器失败")
            if self.app is not None:
                try:
                    self.app.store.close()
                except Exception:
                    log.exception("关闭本地数据库失败")

    def do_save_settings(self, values: dict[str, object], clear_api_key: bool = False) -> None:
        current = settings.load_current(self.cfg)
        old_key = current.get("AI_API_KEY")
        current.update(values)
        if clear_api_key:
            current["AI_API_KEY"] = ""
        elif not values.get("AI_API_KEY"):
            current["AI_API_KEY"] = old_key
        path = settings.save(current)
        settings.reload_into(self.cfg, path)
        self._emit(("settings_saved", "设置已保存并生效"))

    def do_ai_test(self) -> None:
        if not self.cfg.ai_enabled:
            self._emit(("err", "请先保存 API 密钥并开启 AI 解析"))
            return
        self._emit(("info", "正在测试 AI 连接…"))
        self._start_task(self._ai_test_gen(), preserve_session=True)

    def _ai_test_gen(self):
        from ai.client import AIClient
        from ui.ai_worker import CapturedConfig
        cfg = CapturedConfig(self.cfg)
        system, prompt = "你是连通性测试助手", "只回复两个字：正常"
        cancelled = threading.Event()
        client = AIClient(cfg, cancel_event=cancelled)
        reply = yield from self._ai_wait(client.ask, system, prompt, purpose="settings_ping", cancelled=cancelled)
        if reply is not None:
            self._emit(("ok", f"AI 连接成功：{cfg.ai_model}，回复：{reply[:40]}"))

    def do_search(self, text: str) -> None:
        if not text.strip():
            return
        result = self.kb.search(text, course_id=self._cid())
        self._emit(("search_results", self.kb.render_hits(result, limit=6)))

    def do_wrong_list(self) -> None:
        rows = self._store().list_wrong(course_id=self._cid(), limit=40)
        lines = [f"{i}. 错{r['wrong_count']}次 ｜ {one_line(r['stem'])[:50]} ｜ 答案 {r.get('answer') or '—'}"
                 for i, r in enumerate(rows, 1)]
        self._emit(("wrong_results", "\n".join(lines) if lines else "错题本是空的。"))


class BridgeApi:
    """Only bootstrap, poll and validated command dispatch are exposed to JS."""

    def __init__(self, cfg: Config, *, scheduler: Scheduler | None = None) -> None:
        self._cfg = cfg
        self._scheduler = scheduler or DesktopScheduler(cfg, queue.Queue())
        self._out = self._scheduler.out
        self._lock = threading.RLock()
        self._secrets = {str(cfg.ai_api_key), str(cfg.get("AI_API_KEY") or "")} - {""}
        self._log_filter = _RedactingFilter(self._redact)
        self._log_handlers = list(get_logger().handlers)
        for handler in self._log_handlers:
            handler.addFilter(self._log_filter)

    def _close(self) -> None:
        for handler in self._log_handlers:
            handler.removeFilter(self._log_filter)

    def _redact(self, value: object) -> object:
        if isinstance(value, str):
            with self._lock:
                secrets = self._secrets | {self._cfg.ai_api_key} - {""}
            for secret in secrets:
                value = value.replace(secret, "[密钥已隐藏]")
            return value
        if isinstance(value, dict):
            return {str(k): self._redact(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._redact(item) for item in value]
        return value

    def _settings(self) -> dict[str, object]:
        values = settings.load_current(self._cfg)
        for key, meta in settings.EDITABLE.items():
            if meta[0] == "secret":
                values[key] = ""
        return {"values": values, "groups": list(settings.GROUPS),
                "fields": [{"key": key, "kind": kind, "label": label, "group": group}
                           for key, (kind, label, group) in settings.EDITABLE.items()]}

    def _snapshot(self) -> dict[str, object]:
        courses: list[dict[str, object]] = []
        sections: list[dict[str, object]] = []
        course_id: int | None = None
        db_path = self._cfg.db_path.resolve()
        if db_path.is_file():
            with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
                conn.row_factory = sqlite3.Row
                courses = [dict(row) for row in conn.execute("SELECT id,name FROM courses ORDER BY updated_at DESC")]
                current = conn.execute("SELECT value FROM meta WHERE key='current_course'").fetchone()
                if current and str(current["value"]).isdigit():
                    selected = int(current["value"])
                    if any(c["id"] == selected for c in courses):
                        course_id = selected
                if course_id is not None:
                    rows = conn.execute("""SELECT i.id,i.title,i.kind,i.progress,i.done,c.id AS chapter_id
                        FROM items i LEFT JOIN chapters c ON c.course_id=i.course_id
                        AND c.chap_key=i.chapter_key WHERE i.course_id=? ORDER BY i.id LIMIT 5000""", (course_id,))
                    for row in rows:
                        item = dict(row)
                        item["done"] = bool(item["done"])
                        item["title"] = item["title"] or "未命名小节"
                        item["progress"] = float(item["progress"] or 0)
                        sections.append(item)
        sched = self._scheduler
        return {"ready": sched.ready.is_set(),
                "busy": bool(sched.active_action) or sched.login_pending.is_set()
                        or sched.task is not None or not sched.jobs.empty(),
                "login_pending": sched.login_pending.is_set(), "login_state": sched.login_state,
                "initialization_error": sched.error,
                "course_id": course_id, "courses": courses, "sections": sections,
                "stats": {"total": len(sections), "finished": sum(bool(x["done"]) for x in sections)},
                "ai": {"configured": bool(self._cfg.ai_api_key.strip()), "enabled": self._cfg.ai_enabled,
                       "model": self._cfg.ai_model, "base_url": self._cfg.ai_base_url},
                "settings": self._settings()}

    def bootstrap(self) -> dict[str, object]:
        try:
            with self._lock:
                return {"ok": True, "data": self._redact(self._snapshot())}
        except sqlite3.Error:
            log.exception("读取桌面状态失败")
            return {"ok": False, "error": "无法读取本地数据库，请稍后重试或检查文件权限"}

    def poll(self) -> dict[str, object]:
        try:
            with self._lock:
                snapshot = self._redact(self._snapshot())
                events = []
                for _ in range(200):
                    try:
                        event = self._out.get_nowait()
                        level, payload = event
                    except queue.Empty:
                        break
                    events.append({"level": level, "payload": self._redact(payload),
                                   "course_id": getattr(event, "course_id", None),
                                   "request_id": getattr(event, "request_id", None)})
                return {"ok": True, "data": {"events": events, "snapshot": snapshot}}
        except sqlite3.Error:
            log.exception("刷新桌面状态失败")
            return {"ok": False, "error": "无法刷新本地数据，请稍后重试"}

    def command(self, action: object, params: object = None) -> dict[str, object]:
        try:
            if not isinstance(action, str) or action not in _COMMANDS and action != "save_settings":
                raise ValueError("不支持的操作")
            if params is None:
                params = {}
            if not isinstance(params, dict):
                raise ValueError("操作参数必须为键值对象")
            if action == "save_settings":
                if set(params) - {"values", "clear_api_key"}:
                    raise ValueError("包含不支持的参数")
                values = _validate_settings(params.get("values", {}))
                parsed = {"values": values, "clear_api_key": _boolean(params.get("clear_api_key", False))}
                with self._lock:
                    secret = values.get("AI_API_KEY")
                    if isinstance(secret, str) and secret:
                        self._secrets.add(secret)
            else:
                schema = _COMMANDS[action]
                if set(params) - set(schema):
                    raise ValueError("包含不支持的参数")
                parsed = {}
                for key, (validate, default) in schema.items():
                    value = params.get(key, default)
                    if value is _REQUIRED:
                        raise ValueError("缺少必需参数")
                    parsed[key] = validate(value)
            request_id = self._scheduler.submit_request(action, **parsed)
            return {"ok": True, "request_id": request_id}
        except (ValueError, TypeError):
            return {"ok": False, "error": "操作或设置参数无效，请检查输入范围和类型"}
