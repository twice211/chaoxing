# -*- coding: utf-8 -*-
"""utils.cxfont —— 学习通“字体加密”(font-cxsecret) 解码。

超星把部分汉字换成“假码点”,再用内联自定义字体把它们**画成**真字(所以屏幕正常、
DOM 文本乱码)。本模块在页面里:收集内联 @font-face → 取字体 cmap 得到假码点 →
把每个假码点用该字体渲染到 canvas,再和“页面上明文出现过的候选汉字”用系统字体
渲染比对像素 → 得到 假码点→真字 映射,用于还原题干/选项。全程本地,不联网。
"""

from __future__ import annotations

import base64
import io
from functools import lru_cache
from typing import Any, Callable, Optional

from utils.logger import get_logger

log = get_logger("utils.cxfont")

FACES_JS = r"""() => {
  const out = [];
  const push = (fam, src) => {
    const m = (src || '').match(/url\(\s*["']?data:[^,]*base64,([A-Za-z0-9+\/=]+)/);
    if (m && fam) out.push({fam: fam, b64: m[1]});
  };
  for (const s of document.styleSheets) {
    let rules; try { rules = s.cssRules; } catch (e) { continue; }
    for (const r of rules) {
      try { if (r.cssText && /@font-face/i.test(r.cssText))
        push((r.style.getPropertyValue('font-family') || '').replace(/["']/g, '').trim(),
             r.style.getPropertyValue('src')); } catch (e) {}
    }
  }
  for (const st of document.querySelectorAll('style')) {
    const txt = st.textContent || '';
    const re = /@font-face\s*\{[^}]*?font-family\s*:\s*["']?([^"'};\s]+)["']?[^}]*?base64,([A-Za-z0-9+\/=]+)/g;
    let m; while ((m = re.exec(txt))) out.push({fam: m[1], b64: m[2]});
  }
  return out;
}"""

DECODE_JS = r"""async ([family, fakes, extra, fonts]) => {
  try { await document.fonts.load("60px '" + family + "'"); } catch (e) {}
  const text = document.body ? (document.body.innerText || '') : '';
  const set = new Set();
  for (const ch of text) {
    const cp = ch.codePointAt(0);
    if ((cp >= 0x3400 && cp <= 0x9fff) || [0x3001, 0x3002, 0xff08, 0xff09, 0xff1a, 0xff0c].includes(cp)) set.add(cp);
  }
  for (const cp of (extra || [])) set.add(cp);
  for (const f of fakes) set.delete(f);
  const cands = [...set];
  const SIZE = 96, N = 40;
  const cv = document.createElement('canvas'); cv.width = SIZE; cv.height = SIZE;
  const ctx = cv.getContext('2d', {willReadFrequently: true});
  const cv2 = document.createElement('canvas'); cv2.width = N; cv2.height = N;
  const ctx2 = cv2.getContext('2d', {willReadFrequently: true});
  const draw = (cp, ff) => {
    ctx.clearRect(0, 0, SIZE, SIZE);
    ctx.fillStyle = '#000'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.font = '72px ' + ff;
    ctx.fillText(String.fromCodePoint(cp), SIZE / 2, SIZE / 2 + 6);
    const im = ctx.getImageData(0, 0, SIZE, SIZE).data;
    let x0 = SIZE, y0 = SIZE, x1 = -1, y1 = -1;
    for (let y = 0; y < SIZE; y++) for (let x = 0; x < SIZE; x++) {
      if (im[(y * SIZE + x) * 4 + 3] > 24) {
        if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
    }
    if (x1 < x0) return null;
    const mw = x1 - x0 + 1, mh = y1 - y0 + 1, pad = Math.round(0.08 * Math.max(mw, mh));
    const sx = Math.max(0, x0 - pad), sy = Math.max(0, y0 - pad);
    const sw = Math.min(SIZE - sx, mw + 2 * pad), sh = Math.min(SIZE - sy, mh + 2 * pad);
    ctx2.clearRect(0, 0, N, N);
    ctx2.imageSmoothingEnabled = true; ctx2.imageSmoothingQuality = 'high';
    ctx2.drawImage(cv, sx, sy, sw, sh, 0, 0, N, N);
    const d = ctx2.getImageData(0, 0, N, N).data;
    const g = new Float64Array(N * N); let sum = 0;
    for (let i = 0; i < g.length; i++) { g[i] = d[i * 4 + 3]; sum += g[i]; }
    if (sum > 0) for (let i = 0; i < g.length; i++) g[i] /= sum;
    return g;
  };
  const mats = [];
  for (const ff of fonts) for (const cp of cands) { const m = draw(cp, ff); if (m) mats.push([cp, m]); }
  const out = {}; const diag = [];
  for (const f of fakes) {
    const g = draw(f, "'" + family + "'");
    if (!g) { out[f] = f; continue; }
    let bd = 1e9, bc = -1, bd2 = 1e9;
    for (const [cp, m] of mats) {
      let s = 0; for (let j = 0; j < g.length; j++) { const t = g[j] - m[j]; s += t * t; }
      if (s < bd) { bd2 = bd; bd = s; bc = cp; } else if (s < bd2) { bd2 = s; }
    }
    out[f] = (bc >= 0 && bd < 0.015) ? bc : f;
    diag.push([f, bc, +bd.toFixed(4), +bd2.toFixed(4)]);
  }
  return {map: out, diag, n: mats.length};
}"""


@lru_cache(maxsize=32)
def _font_cmap(b64: str) -> tuple:
    try:
        from fontTools.ttLib import TTFont
        f = TTFont(io.BytesIO(base64.b64decode(b64)))
        return tuple(sorted((f.getBestCmap() or {}).keys()))
    except Exception:
        log.debug("解析加密字体失败", exc_info=True)
        return ()


BASE_FONTS = ['"Microsoft YaHei"', '"SimSun"']

_CACHE: dict[str, dict] = {}


@lru_cache(maxsize=1)
def _common_chars() -> str:
    """GB2312 全部汉字(约 6.7k 常用字),作为像素比对的候选集。"""
    out = []
    for hi in range(0xB0, 0xF8):
        for lo in range(0xA1, 0xFF):
            try:
                c = bytes([hi, lo]).decode("gb2312")
            except Exception:
                continue
            if 0x4E00 <= ord(c) <= 0x9FFF:
                out.append(c)
    return "".join(out)


def build_decoder(context: Any, extra_candidates: str = "", debug: bool = False) -> Optional[Callable[[str], str]]:
    """给定 frame/page,返回解码函数(若无加密字体则返回 None)。结果按字体内容缓存。"""
    try:
        faces = context.evaluate(FACES_JS) or []
    except Exception:
        return None
    if not faces:
        return None
    mapping: dict[int, int] = {}
    for face in faces:
        b64 = face.get("b64", "")
        fam = face.get("fam", "")
        if not b64:
            continue
        key = f"{fam}|{len(b64)}|{b64[:48]}|{b64[-48:]}"
        cached = _CACHE.get(key)
        if cached is not None:
            mapping.update(cached)
            continue
        cps = _font_cmap(b64)
        if not cps:
            continue
        try:
            r = context.evaluate(DECODE_JS, [fam, list(cps),
                                             [ord(c) for c in (extra_candidates + _common_chars()) if ord(c) > 0x2000],
                                             BASE_FONTS])
        except Exception:
            log.debug("cxfont canvas 解码失败", exc_info=True)
            r = None
        m: dict[int, int] = {}
        if isinstance(r, dict):
            if debug:
                for f, bc, d, d2 in r.get("diag", []):
                    log.info("cxfont: %s(U+%04X) -> %s(U+%04X) d=%s d2=%s",
                             chr(f), f, chr(bc) if bc else "?", bc or 0, d, d2)
            m = {int(k): int(v) for k, v in (r.get("map") or {}).items()}
            _CACHE[key] = m
        mapping.update(m)
    if not mapping:
        return None
    log.info("字体加密解码映射：%s 个字符", len(mapping))

    def decode(text: Any) -> Any:
        if not isinstance(text, str) or not text:
            return text
        return "".join(chr(mapping[ord(c)]) if ord(c) in mapping else c for c in text)

    return decode


def apply(item: dict, decode: Callable[[str], str]) -> dict:
    """就地解码一道原始题目里的文字字段。"""
    if item.get("stem"):
        item["stem"] = decode(item["stem"])
    for o in item.get("options") or []:
        if isinstance(o, dict):
            for key in ("text", "raw"):
                if o.get(key):
                    o[key] = decode(o[key])
    for key in ("my_answer", "ref_answer"):
        if item.get(key):
            item[key] = decode(item[key])
    return item
