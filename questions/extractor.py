# -*- coding: utf-8 -*-
"""
questions.extractor —— 从学习通页面提取题目（单选/多选/判断/填空/简答）

两级策略：
1. 首选 EXTRACT_QUESTIONS_JS：在浏览器里按 DOM 结构解析（含真实 input 状态）；
2. 兜底 BeautifulSoup：当 JS 因选择器改版抓不到时，用 HTML 静态解析尽力恢复。
两种结果统一成 Question 对象，并用指纹去重、按页面位置排序。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from bs4 import BeautifulSoup

from browser.js import EXTRACT_QUESTIONS_JS
from course.selectors import SELECTORS
from questions.models import Question, dedupe_questions, normalize_kind, strip_option_prefix
from utils import cxfont
from utils.logger import get_logger
from utils.text import clean_text, one_line, strip_html

log = get_logger("questions.extractor")

OPTION_LINE_RE = re.compile(r"^\s*([A-H])\s*[\.、）\)]\s*(.+)$")
_OCR_PREFIX_RE = re.compile(r"^\s*([A-Ha-h])\s*[\.、）\)]\s*")


def _merge_ocr(items: list[dict], ocr: list[dict]) -> None:
    """把视觉识别的正确文字合并回 DOM 结构条目(保留 id/name/nidx 供点击定位)。

    配对优先按题数一致(同序),否则按题号;合并成功的条目标记 _ocr,跳过像素解码避免二次改动。
    """
    by_no: dict[str, dict] = {}
    for o in ocr:
        n = re.sub(r"\D", "", str(o.get("no") or ""))
        if n:
            by_no[n] = o
    pairs: list[tuple[dict, dict]] = []
    if len(ocr) == len(items):
        pairs = list(zip(items, ocr))
    else:
        for it in items:
            n = re.sub(r"\D", "", str(it.get("no") or ""))
            if n in by_no:
                pairs.append((it, by_no[n]))
    for it, o in pairs:
        stem = str(o.get("stem") or "").strip()
        if stem:
            it["stem"] = stem
            it["_ocr"] = True
        opts = o.get("options")
        dom_opts = it.get("options") or []
        if isinstance(opts, list) and opts:
            if len(opts) == len(dom_opts):
                for do, oo in zip(dom_opts, opts):
                    if not isinstance(do, dict):
                        continue
                    text = _OCR_PREFIX_RE.sub("", str(oo).replace("\n", " ").strip())
                    if text:
                        do["text"] = text
                        do["raw"] = str(oo)
            elif not dom_opts:
                # DOM 完全没解析出选项：用视觉结果兜底（可显示/给 AI，点击可能无效）
                it["options"] = [
                    {"label": ("ABCDEFGH"[j] if j < 8 else str(j)),
                     "text": _OCR_PREFIX_RE.sub("", str(oo).strip()),
                     "checked": False, "value": "", "id": "", "name": "", "nidx": -1,
                     "input_type": "text", "click_sel": ""}
                    for j, oo in enumerate(opts)]
                it["_ocr"] = True


class QuestionExtractor:
    def __init__(self, cfg: Any = None, max_questions: int = 200) -> None:
        self.cfg = cfg
        self.max_questions = int(max_questions)
        self.ai: Any = None                 # 可注入 AIClient；None 则按需惰性建（配置了 AI 才用）
        self._lazy_ai: Any = None

    # ------------------------------------------------------------ AI 视觉（仅在字体加密时启用）
    def _ai_client(self) -> Any:
        if self.ai is not None:
            return self.ai
        if self._lazy_ai is None and self.cfg is not None:
            try:
                from ai.client import AIClient
                self._lazy_ai = AIClient(self.cfg)
            except Exception:
                self._lazy_ai = False
        return self._lazy_ai or None

    def _try_vision(self, frame: Any, sig: str = "") -> list[dict] | None:
        try:
            ai = self._ai_client()
            if ai is None or not ai.enabled:
                return None
            from utils import visocr
            return visocr.recognize_questions(ai, frame, sig=sig)
        except Exception as exc:
            log.debug("视觉识别跳过：%s", str(exc)[:120])
            return None

    # ------------------------------------------------------------ JS 主路径
    def extract(self, page: Any) -> list[Question]:
        payload = {
            "roots": SELECTORS["question_root"],
            "stems": SELECTORS["question_stem"],
            "options": SELECTORS["question_option"],
            "qnos": SELECTORS["question_no"],
            "scores": SELECTORS["question_score"],
            "correctFlags": SELECTORS["result_correct_flag"],
            "wrongFlags": SELECTORS["result_wrong_flag"],
            "answerText": SELECTORS["result_answer_text"],
            "userAnswerText": SELECTORS["result_user_answer_text"],
            "max": self.max_questions,
        }
        results: list[Question] = []
        urls: list[str] = []
        try:
            frames = list(page.frames)
        except Exception:
            frames = []
        # 必须扫描**所有** frame：学习通随堂弹题在播放器 iframe 内，而页面其它位置
        # 也有题目容器；以前“扫到第一个有结果的 frame 就 break”，弹窗永远扫不到。
        seen_fp: set[str] = set()
        for fi, frame in enumerate(frames):
            try:
                urls.append(frame.url or "")
            except Exception:
                continue
            try:
                raw = frame.evaluate(EXTRACT_QUESTIONS_JS, payload)
            except Exception as exc:
                log.debug("frame%s JS 提取失败：%s", fi, exc)
                raw = None
            decode = None
            if raw:
                try:
                    decode = cxfont.build_decoder(frame)      # 学习通字体加密→像素比对还原
                except Exception:
                    decode = None
                    log.debug("cxfont 解码器构建失败", exc_info=True)
            if raw and decode:
                # 确实被字体加密：AI 配置了就改用视觉识别拿准确文字；没配就沿用像素/原始
                try:
                    import hashlib
                    import json as _json
                    sig = hashlib.md5(_json.dumps(raw, ensure_ascii=False)[:4000].encode("utf-8")).hexdigest()[:16]
                    ocr = self._try_vision(frame, sig)
                except Exception:
                    ocr = None
                if ocr:
                    _merge_ocr(raw, ocr)
            got = 0
            for item in raw or []:
                if decode and not item.get("_ocr"):
                    cxfont.apply(item, decode)
                q = Question.from_raw(item)
                try:
                    q.section_url = frame.url or page.url
                except Exception:
                    pass
                if q.fp in seen_fp:
                    continue
                seen_fp.add(q.fp)
                results.append(q)
                got += 1
            if got:
                log.debug("frame%s（%s）贡献 %s 道题", fi, str(getattr(frame, "url", ""))[:60], got)
        if not results:
            log.info("JS 提取未命中，启用 HTML 兜底解析")
            for frame in frames or []:
                try:
                    html = frame.content()
                except Exception:
                    continue
                parsed = self.parse_html(html)
                for q in parsed:
                    q.section_url = q.section_url or (frame.url or "")
                results.extend(parsed)
                if results:
                    break
        results = dedupe_questions(results)
        results.sort(key=lambda q: (q.top, q.no))
        log.info("提取到 %s 道题目", len(results))
        return results

    # ------------------------------------------------------------ BS4 兜底
    def parse_html(self, html: str) -> list[Question]:
        if not html:
            return []
        soup = BeautifulSoup(html, "lxml" if _lxml_available() else "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        # 隐藏内容（随堂弹题会预先埋在 DOM 里）不参与解析，避免提前误报
        for el in soup.select("[style]"):
            st = str(el.get("style") or "").replace(" ", "").lower()
            if "display:none" in st or "visibility:hidden" in st or "opacity:0" in st:
                el.decompose()
        roots: list[Any] = []
        for sel in SELECTORS["question_root"]:
            found = [n for n in soup.select(_css_safe(sel)) if one_line(n.get_text())]
            if found:
                roots = found
                break
        if not roots:
            # 兜底：以 input/textarea 的 name 分组反推容器
            groups: dict[str, list[Any]] = {}
            for inp in soup.select("input, textarea"):
                if (inp.get("type") or "").lower() in ("hidden", "submit", "button", "reset"):
                    continue
                key = one_line(inp.get("name") or inp.get("id") or "_")
                groups.setdefault(key, []).append(inp)
            for inputs in groups.values():
                node = inputs[0]
                for _ in range(6):
                    node = node.parent
                    if node is None:
                        break
                    if len(one_line(node.get_text())) > 15:
                        roots.append(node)
                        break
        out: list[Question] = []
        for idx, root in enumerate(roots[: self.max_questions]):
            q = self._parse_one(root, idx)
            if q and len(q.stem) > 6:
                out.append(q)
        return out

    def _parse_one(self, root: Any, idx: int) -> Question | None:
        stem = ""
        for sel in SELECTORS["question_stem"]:
            node = root.select_one(_css_safe(sel))
            if node and one_line(node.get_text()):
                stem = clean_text(node.get_text())
                break
        options: list[dict[str, Any]] = []
        inputs = [i for i in root.select("input[type=radio],input[type=checkbox]")]
        for i, inp in enumerate(inputs):
            label = ""
            if inp.get("id"):
                lab = root.select_one(f"label[for='{inp['id']}']")
                if lab:
                    label = one_line(lab.get_text())
            if not label:
                near = inp.find_parent("label") or inp.find_next(string=True)
                label = one_line(near.get_text() if hasattr(near, "get_text") else str(near))
            options.append(
                {
                    "label": chr(65 + i), "text": strip_option_prefix(label),
                    "checked": bool(inp.has_attr("checked")), "value": inp.get("value", ""),
                }
            )
        blanks = [one_line(t.get_text()) or one_line(t.get("value", ""))
                  for t in root.select("textarea,input[type=text]")]
        if not stem:
            full = clean_text(root.get_text())
            if options:
                cut = full.find(options[0]["text"][:12]) if options[0]["text"] else -1
                stem = full[:cut] if cut > 6 else full[:600]
            else:
                stem = full[:800]
        kind = "unknown"
        if any(i.get("type") == "checkbox" for i in inputs):
            kind = "multi"
        elif any(i.get("type") == "radio" for i in inputs):
            kind = "judge" if _looks_judge(options, stem) else "single"
        elif len([b for b in blanks if b]) >= 1 or blanks:
            kind = "blank" if re.search(r"（\s*）|\(\s*\)|_{2,}", stem) else "short"
        kind = normalize_kind(kind, [stem[:80], one_line(root.get("class", ""))[0] if root.get("class") else ""])
        my_answer = "".join(o["label"] for o in options if o["checked"])
        return Question(
            stem=stem, kind=kind, no=str(idx + 1), options=options, my_answer=my_answer,
            analysis="", raw={"blanks": blanks, "detect": "bs4"}, detect_by="bs4",
        )

    # ------------------------------------------------------------ 判分结果只读导入
    def read_grading(self, page: Any) -> list[dict[str, Any]]:
        """读取“已提交/已批改”页面上的对错与参考答案（只读，用于自动整理错题）。"""
        out = []
        for q in self.extract(page):
            out.append(
                {
                    "no": q.no, "fp": q.fp, "stem": q.stem, "kind": q.kind,
                    "is_right": q.is_right, "ref_answer": q.ref_answer,
                    "user_answer": q.my_answer, "options": q.option_texts,
                }
            )
        return out


def _looks_judge(options: Sequence[dict[str, Any]], stem: str) -> bool:
    texts = [one_line(o.get("text", "")).strip("。.！!") for o in options]
    if len(texts) == 2 and set(texts) <= {"正确", "错误", "对", "错", "√", "×", "T", "F"}:
        return True
    return bool(re.search(r"(判断|下列说法正确|是否正确)", stem or "")) and len(texts) <= 2


def _lxml_available() -> bool:
    try:
        import lxml  # noqa: F401

        return True
    except Exception:
        return False


def _css_safe(sel: str) -> str:
    """把 Playwright 的 :has-text() 等伪类过滤掉，避免 BS4 报错。"""
    sel = re.sub(r":has-text\([^)]*\)", "", sel)
    sel = re.sub(r":visible|:enabled|:disabled", "", sel)
    sel = re.sub(r"nth=\d+", "", sel)
    sel = sel.replace(">>", " ").strip()
    return sel or "body"
    return None
