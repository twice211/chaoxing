# -*- coding: utf-8 -*-
"""browser.quiz —— 学习通视频随堂弹题(ans-videoquiz)的读/写小工具（跨 frame、只点题自己的元素）。

供 PopupWatcher 做“答错自动换选项重试”：选中第 k 个选项 → 提交 → 读“回答正确/错误”。
"""

from __future__ import annotations

from typing import Any

from browser import actions as A
from utils.logger import get_logger

log = get_logger("browser.quiz")

OPT_SELECTOR = "input[name='ans-videoquiz-opt']"

# 逐 .tkItem 解析视频弹题：题干只取本小问、选项只取本题自己的 li，避免把字幕/设置控件
# 或整段说明并进来（那会造出“超长题干+7 选项+带图”的假题，被严格真题判定挡掉）。
QUIZ_ITEMS_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    try {
      const r = el.getBoundingClientRect();
      if (r.width < 4 || r.height < 4) return false;
      let n = el;
      for (let i = 0; i < 8 && n; i++) { const s = getComputedStyle(n); if (s.display === 'none' || s.visibility === 'hidden') return false; n = n.parentElement; }
      return true;
    } catch (e) { return true; }
  };
  const out = [];
  document.querySelectorAll('.ans-videoquiz').forEach((quiz) => {
    let items = Array.from(quiz.querySelectorAll('.tkItem'));
    if (!items.length) items = [quiz];
    items.forEach((item, qi) => {
      const stemEl = item.querySelector('.tkItem_title') || item.querySelector('.tkTopic_title') || quiz.querySelector('.tkTopic_title');
      const stem = norm(stemEl ? (stemEl.getAttribute('title') || stemEl.innerText) : '');
      const optEls = Array.from(item.querySelectorAll('.tkItem_ul li, li.ans-videoquiz-opt'));
      const options = [];
      optEls.forEach((li, oi) => {
        const inp = li.querySelector('input');
        const t = norm(li.innerText || li.textContent || '');
        if (!t) return;
        options.push({ text: t, value: inp ? (inp.value || '') : '', id: inp ? (inp.id || '') : '',
                       name: inp ? (inp.getAttribute('name') || '') : '', checked: inp ? !!inp.checked : false,
                       input_type: inp ? (inp.type || '') : '', nidx: oi });
      });
      if (!stem || !options.length) return;
      const strip = (s) => norm(s).replace(/^[A-Za-z][、.\)]\s*/, '');
      const judge = options.length === 2 && /^(对|正确|√|是)$/.test(strip(options[0].text)) && /^(错|错误|×|否)$/.test(strip(options[1].text));
      const kind = judge ? 'judge' : (item.querySelector('input[type=checkbox]') ? 'multi' : 'single');
      const r = item.getBoundingClientRect();
      out.push({ no: String(qi + 1), kind, stem, options, has_image: !!item.querySelector('img'),
                 my_answer: '', ref_answer: '', score: '', is_right: null,
                 top: Math.round(r.top + window.scrollY), text_len: stem.length,
                 inView: visible(item), modal: true, source: 'videoquiz', detect_by: 'ans-videoquiz' });
    });
  });
  return out;
}
"""


def extract_items(page: Any) -> list[dict]:
    """跨 frame 逐 .tkItem 抽视频弹题，返回可直接喂 Question.from_raw 的原始字典列表。
    页面没有 ans-videoquiz 结构时返回 []（调用方回退通用抽取）。"""
    out: list[dict] = []
    for frame in A.frames_of(page):
        try:
            rows = frame.evaluate(QUIZ_ITEMS_JS) or []
        except Exception:
            continue
        try:
            url = frame.url or ""
        except Exception:
            url = ""
        for r in rows:
            if isinstance(r, dict):
                r["_url"] = url
                out.append(r)
    return out


def find_quiz_inputs(page: Any) -> list:
    """跨 frame 找到视频弹题的选项输入（按组内顺序）。找不到返回 []。"""
    for frame in A.frames_of(page):
        try:
            els = frame.query_selector_all(OPT_SELECTOR)
        except Exception:
            continue
        if els:
            return list(els)
    return []


def pick_option(inputs: list, k: int) -> None:
    """勾选第 k 个选项：先点整行 li/label（学习通靠 label 的 JS 才认勾选，只点 input 会被重置），
    再点 input 兜底；若仍未选中则强制 checked 并派发 change/click 事件。"""
    if 0 <= k < len(inputs):
        try:
            inputs[k].evaluate(
                "node => { const li=node.closest('li')||node.parentElement; if(li) li.click(); node.click();"
                " if(!node.checked){ node.checked=true;"
                " node.dispatchEvent(new Event('input',{bubbles:true}));"
                " node.dispatchEvent(new Event('change',{bubbles:true})); } }")
        except Exception as exc:
            log.debug("点选第 %s 项失败：%s", k, exc)


def is_checked(inputs: list, k: int) -> bool:
    """该选项当前是否处于选中状态（提交前复查用）。"""
    try:
        return bool(inputs[k].evaluate("node => !!node.checked"))
    except Exception:
        return False


def submit_quiz(page: Any) -> bool:
    """点“提交”（仅视频弹题自己的 #videoquiz-submit），不点“继续”。"""
    _f, el = A.in_frames(page, ["#videoquiz-submit", "a.ans-videoquiz-submit"])
    if el is None:
        return False
    try:
        el.evaluate("node => node.click()")
        return True
    except Exception as exc:
        log.debug("弹题提交失败：%s", exc)
        return False


def save_quiz(page: Any) -> bool:
    """“暂时保存/自动保存”：只存勾选的答案，不交卷。"""
    _f, el = A.in_frames(page, ["#tempsave", "#videoquiz-save", "a.ans-videoquiz-save",
                                "a:has-text('自动保存')", "a:has-text('暂时保存')", "span:has-text('暂时保存')"])
    if el is None:
        return False
    try:
        el.evaluate("node => node.click()")
        return True
    except Exception as exc:
        log.debug("弹题保存失败：%s", exc)
        return False


def read_result(page: Any) -> str:
    """读视频弹题结果：出现“回答错误”→'wrong'；否则''（表示没有报错）。
    答对时平台不显示任何文字、只让视频继续播放，所以调用方据此把“没有 wrong”当作答对。"""
    js = ("() => { const vis=s=>{const e=document.querySelector(s);return !!e&&(e.offsetParent!==null||"
          "((e.textContent||'').trim().length&&getComputedStyle(e).display!=='none'));};"
          "if(vis('.spanNot')||vis('#spanNot')||vis('.spanNotBack')) return 'wrong';return ''; }")
    for frame in A.frames_of(page):
        try:
            r = frame.evaluate(js)
        except Exception:
            r = None
        if r == "wrong":
            return "wrong"
    return ""


def click_continue(page: Any) -> bool:
    """点弹题自己的“继续”恢复播放（仅当可见）。"""
    _f, el = A.in_frames(page, ["#videoquiz-continue", "a.ans-videoquiz-continue"])
    if el is None:
        return False
    try:
        if el.is_visible():
            el.evaluate("node => node.click()")
        return True
    except Exception as exc:
        log.debug("点继续失败：%s", exc)
        return False
