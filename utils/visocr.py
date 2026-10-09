# -*- coding: utf-8 -*-
"""utils.visocr —— 题目区截图 + AI 视觉模型识别文字。

仅在学习通字体加密（题干乱码）且 AI 已配置时调用；不加任何新配置项：
先试当前 AI_MODEL，若不支持图像自动改试常见 qwen-vl 模型；全部失败返回 None，
调用方保持原样显示（像素解码结果或乱码），绝不卡流程。
"""

from __future__ import annotations

import base64
import time
from typing import Any

from utils.logger import get_logger

log = get_logger("utils.visocr")

PROMPT = (
    "这是学习通测验题目截图。识别其中全部题目，严格只输出 JSON 数组，不要任何解释：\n"
    '[{"no":"1","kind":"single|multi|judge|blank|short","stem":"题干全文",'
    '"options":["A. 选项文本","B. 选项文本"]}]\n'
    '规则：判断题 options 固定为 ["A. 对","B. 错"]；填空题 options 为空数组并把空位写作 ____；'
    "看不清的题跳过，不要编造。"
)

MARK_LCA_JS = r"""(sel)=>{
  let els;
  try { els=[...document.querySelectorAll(sel)].filter(e=>e && e.offsetParent!==null); } catch(e){ return false; }
  if(!els.length) return false;
  const anc=new Set(); let n=els[0];
  while(n){ anc.add(n); n=n.parentElement; }
  let node=els[0];
  for(const e of els.slice(1)){
    let m=e; while(m && !anc.has(m)) m=m.parentElement;
    if(!m){ node=document.body; break; }
    node=m;
  }
  node.setAttribute('data-cxocr','1');
  return true;
}"""

UNMARK_JS = "()=>{const e=document.querySelector('[data-cxocr]');if(e)e.removeAttribute('data-cxocr');}"

_SELECTORS = (".ans-videoquiz", ".TiMu", ".questionLi", ".question-item")
_CACHE: dict[str, tuple[float, list]] = {}
_TTL = 120.0


def recognize_questions(ai: Any, frame: Any, sig: str = "") -> list[dict] | None:
    """截图该 frame 的题目公共容器 → 视觉模型转写。sig 为题目内容指纹(换题即失效缓存)。"""
    key = f"{str(getattr(frame, 'url', '') or '')[:160]}|{sig}"
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < _TTL:
        return hit[1]
    b64 = capture_question_image(frame)
    if b64 is None:
        return None
    out = recognize_image(ai, b64)
    if out:
        _CACHE[key] = (time.time(), out)
    return out


def capture_question_image(frame: Any) -> str | None:
    """Capture plain base64 on the browser owner thread."""
    try:
        marked = False
        for sel in _SELECTORS:
            try:
                if frame.evaluate(MARK_LCA_JS, sel):
                    marked = True
                    break
            except Exception:
                continue
        if not marked:
            return None
        try:
            el = frame.query_selector("[data-cxocr='1']")
            if el is None:
                return None
            png = el.screenshot()
        finally:
            try:
                frame.evaluate(UNMARK_JS)
            except Exception:
                pass
        b64 = base64.b64encode(png).decode("ascii")
    except Exception as exc:
        log.debug("题目截图失败：%s", exc)
        return None

    return b64


def recognize_image(ai: Any, b64: str) -> list[dict] | None:
    """Recognize plain image data; never accepts a browser object."""
    models: list[str] = []
    try:
        if ai.cfg.ai_model:
            models.append(str(ai.cfg.ai_model))
    except Exception:
        pass
    for m in ("qwen-vl-plus", "qwen-vl-max"):
        if m not in models:
            models.append(m)

    from ai.client import _extract_json

    last_err = ""
    for m in models:
        try:
            text = ai.ask_vision(b64, PROMPT, purpose="visocr", model=m)
            data = _extract_json(text)
            if isinstance(data, dict):
                data = [data]
            if isinstance(data, list) and data:
                out = [d for d in data if isinstance(d, dict)]
                if out:
                    log.info("视觉识别成功（模型 %s，%s 题，图 %sKB）", m, len(out), len(b64) // 1024)
                    return out
        except Exception as exc:
            last_err = str(exc)[:160]
            log.debug("视觉识别失败（模型 %s）：%s", m, last_err)
    if last_err:
        log.info("视觉识别不可用，保持原显示：%s", last_err)
    return None
