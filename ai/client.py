# -*- coding: utf-8 -*-
"""
ai.client —— OpenAI 兼容聊天接口客户端（带重试、超时、调用台账）

兼容任意提供 /chat/completions 的服务：OpenAI、DeepSeek、Moonshot、智谱、
豆包方舟、通义千问兼容模式、本地 Ollama /v1 等，只需改 AI_BASE_URL/AI_MODEL。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import requests

from utils.logger import get_logger
from utils.retry import RetryExhausted, retry_call

log = get_logger("ai.client")

JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


class AIUnavailable(RuntimeError):
    """AI 未启用/未配置密钥。"""


class AIRequestError(RuntimeError):
    """可重试的网络/服务异常。"""


@dataclass
class AIUsage:
    calls: int = 0
    failures: int = 0
    total_ms: float = 0.0
    total_chars: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "calls": self.calls, "failures": self.failures,
            "avg_ms": round(self.total_ms / self.calls, 1) if self.calls else 0.0,
            "total_chars": self.total_chars,
        }


def _extract_json(text: str) -> Any:
    """从模型输出里稳健地抽出 JSON（容忍 ```json 包裹与前后废话）。"""
    if not text:
        raise ValueError("空响应")
    m = JSON_FENCE_RE.search(text)
    raw = (m.group(1) if m else text).strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    start = min((i for i in (raw.find("{"), raw.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError("响应中未找到 JSON")
    opener = raw[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    for i in range(start, len(raw)):
        if raw[i] == opener:
            depth += 1
        elif raw[i] == closer:
            depth -= 1
            if depth == 0:
                return json.loads(raw[start : i + 1])
    raise ValueError("JSON 括号未闭合")


class AIClient:
    def __init__(self, cfg: Any, store: Any | None = None) -> None:
        self.cfg = cfg
        self.store = store
        self.usage = AIUsage()
        self._session = requests.Session()

    # ------------------------------------------------------------ 基础
    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("AI_ENABLE")) and bool(self.cfg.ai_api_key)

    def _url(self) -> str:
        return f"{str(self.cfg.ai_base_url).rstrip('/')}/chat/completions"

    def _check(self) -> None:
        if not self.cfg.get("AI_ENABLE"):
            raise AIUnavailable("AI_ENABLE=False，已在配置中关闭 AI 功能")
        if not self.cfg.ai_api_key:
            raise AIUnavailable("未配置 AI_API_KEY（请设置环境变量或写入 user_config.py）")

    # ------------------------------------------------------------ 调用
    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        purpose: str = "general",
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        timeout: float | None = None,
        attempts: int | None = None,
    ) -> str:
        """发送一次对话请求，失败自动重试；全部失败抛 AIUnavailable。"""
        self._check()
        attempts = max(1, int(attempts or self.cfg.get("AI_MAX_RETRY") or 3))
        to = float(timeout or self.cfg.get("AI_TIMEOUT_SEC") or 60)
        body = {
            "model": model or self.cfg.ai_model,
            "messages": list(messages),
            "temperature": float(self.cfg.get("AI_TEMPERATURE") if temperature is None else temperature),
            "max_tokens": int(max_tokens or self.cfg.get("AI_MAX_TOKENS") or 1024),
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.cfg.ai_api_key}",
            "Content-Type": "application/json",
        }
        est_chars = sum(len(str(m.get("content", ""))) for m in messages)
        t0 = time.time()
        try:
            text = retry_call(
                self._once, body, headers, purpose=purpose, timeout=to,
                attempts=attempts, delay=1.5, backoff=float(self.cfg.get("AI_RETRY_BACKOFF") or 2.0),
                exceptions=(AIRequestError, requests.RequestException), name=f"ai:{purpose}",
            )
        except RetryExhausted as exc:
            self.usage.failures += 1
            self._log(purpose, "error", (time.time() - t0) * 1000, est_chars, str(exc))
            raise AIUnavailable(f"AI 请求失败（已重试 {attempts} 次）：{exc}") from exc
        ms = (time.time() - t0) * 1000
        self.usage.calls += 1
        self.usage.total_ms += ms
        self.usage.total_chars += est_chars + len(text)
        self._log(purpose, "ok", ms, est_chars)
        return text

    def _once(self, body: dict[str, Any], headers: dict[str, str], purpose: str = "",
              timeout: float | None = None) -> str:
        timeout = float(timeout or self.cfg.get("AI_TIMEOUT_SEC") or 60)
        try:
            resp = self._session.post(self._url(), json=body, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            raise AIRequestError(f"网络异常：{exc}") from exc
        if resp.status_code in (408, 429, 500, 502, 503, 504):
            raise AIRequestError(f"服务暂时不可用 HTTP {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 400:
            # 4xx 多为配置/权限问题，重试无意义：抛不可重试异常
            raise RuntimeError(f"AI 接口返回 HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
        except Exception as exc:
            raise AIRequestError(f"响应不是 JSON：{resp.text[:200]}") from exc
        try:
            choice = data["choices"][0]
            msg = choice.get("message") or {}
            text = msg.get("content") or msg.get("reasoning_content") or ""
        except Exception as exc:
            raise AIRequestError(f"响应结构异常：{json.dumps(data, ensure_ascii=False)[:300]}") from exc
        text = str(text).strip()
        if not text:
            raise AIRequestError("AI 返回空内容")
        return text

    def ask(self, system: str, user: str, purpose: str = "qa", **kw: Any) -> str:
        return self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                         purpose=purpose, **kw)

    def ask_vision(self, image_b64: str, prompt: str, purpose: str = "vision",
                   system: str = "你是试卷识别助手，严格按用户要求输出 JSON。", **kw: Any) -> str:
        """带图请求（OpenAI 兼容 image_url 格式），用于识别被字体加密的题目截图。"""
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        ]
        return self.chat([{"role": "system", "content": system}, {"role": "user", "content": content}],
                         purpose=purpose, timeout=45.0, attempts=1, **kw)

    def ask_json(self, system: str, user: str, purpose: str = "json", retries: int = 2, **kw: Any) -> Any:
        """要求 JSON 输出；解析失败时自动追问一次“只输出 JSON”。"""
        text = self.ask(system, user, purpose=purpose, **kw)
        for _ in range(max(1, retries)):
            try:
                return _extract_json(text)
            except Exception as exc:
                log.warning("AI JSON 解析失败（%s），重试中", exc)
                text = self.chat(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                        {"role": "assistant", "content": text[:2000]},
                        {"role": "user", "content": "上一条回复无法解析为 JSON，请**只输出 JSON**，不要任何其他文字。"},
                    ],
                    purpose=purpose + "_retry",
                    **kw,
                )
        raise AIRequestError("AI 多次返回仍无法解析为 JSON")

    def _log(self, purpose: str, status: str, ms: float, est_chars: int, error: str = "") -> None:
        if self.store is not None:
            try:
                self.store.log_ai_call(purpose, str(self.cfg.ai_model), status, ms, est_chars, error)
            except Exception:
                log.debug("AI 台账写入失败", exc_info=True)


def messages_preview(messages: Iterable[dict[str, str]]) -> str:
    return "\n".join(f"[{m['role']}] {m['content'][:120]}" for m in messages)