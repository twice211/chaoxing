# -*- coding: utf-8 -*-
"""
browser.js —— 在页面里执行的提取脚本（只读）

约定：本文件所有 JS 只做“读取文本/结构”，绝不修改 DOM、不派发输入事件，
因此可以安全地用于考试页面（不会改动考试页面布局，也不会替用户作答）。
"""

from __future__ import annotations

# ------------------------------------------------------ 收集页面上的可跳转链接
# 返回 [{text,url,tag,cls}]，供章节/任务列表解析使用（不依赖具体 class 名）
COLLECT_LINKS_JS = r"""
(cfg) => {
  const opts = cfg || {};
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  const seen = new Set();
  const groupSel = opts.groupSelectors || [];
  const matches = (el, selector) => {
    const base = selector.replace(/\s+a$/, '');      // ".chapter h3 a" -> ".chapter h3"
    try {
      if (el.matches && (el.matches(selector) || el.matches(base))) return true;
      if (el.parentElement && el.parentElement.matches &&
          (el.parentElement.matches(selector) || el.parentElement.matches(base))) return true;
    } catch (e) { /* 非法选择器忽略 */ }
    return false;
  };
  const isGroup = (el) => groupSel.some((s) => matches(el, s));
  const push = (el, url) => {
    const raw = url ? String(url) : '';
    const pseudo = !raw || raw.indexOf('javascript:') === 0 || raw.charAt(0) === '#';
    const text = norm(el.innerText || el.getAttribute('title') || el.getAttribute('alt'));
    if (pseudo && !text) return;                      // 既无链接也无文字，丢弃
    const key = (pseudo ? '' : raw) + '|' + text.slice(0, 40);
    if (seen.has(key)) return;
    seen.add(key);
    let top = 0;
    try { top = Math.round(el.getBoundingClientRect().top + window.scrollY); } catch (e) {}
    out.push({
      text: text.slice(0, 200),
      url: pseudo ? '' : raw,
      tag: el.tagName ? el.tagName.toLowerCase() : '',
      cls: (el.className || '').toString().slice(0, 120),
      group: isGroup(el) ? 1 : 0,
      top: top
    });
  };
  document.querySelectorAll('a[href]').forEach((a) => push(a, a.href));
  document.querySelectorAll('[onclick]').forEach((el) => {
    const raw = el.getAttribute('onclick') || '';
    const m = raw.match(/https?:\/\/[^'"\s)]+/);
    push(el, m ? m[0] : '');
  });
  ['data-url', 'data-href', 'data-link', 'data-src'].forEach((attr) => {
    // 只认“像 URL”的值；学习通很多元素带 data="<jobId>" 这类纯 ID，不能当链接用
    document.querySelectorAll('[' + attr + ']').forEach((el) => {
      const v = el.getAttribute(attr) || '';
      if (/^https?:\/\//.test(v) || /^\//.test(v)) push(el, v.startsWith('http') ? v : (location.origin + v));
    });
  });
  document.querySelectorAll('[data]').forEach((el) => {
    const v = el.getAttribute('data') || '';
    if (/^https?:\/\//.test(v) || /^\//.test(v)) push(el, v.startsWith('http') ? v : (location.origin + v));
  });
  return out;
}
"""

# ------------------------------------------------------ 视频状态只读探针
VIDEO_STATE_JS = r"""
(selectorList) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  let v = null;
  for (const s of selectorList) {
    const el = document.querySelector(s);
    if (el && el.tagName && el.tagName.toLowerCase() === 'video') { v = el; break; }
    if (el && el.querySelector) { const inner = el.querySelector('video'); if (inner) { v = inner; break; } }
  }
  if (!v) { v = document.querySelector('video'); }
  if (!v) return {found: false};
  const d = Number(v.duration || 0), c = Number(v.currentTime || 0);
  return {
    found: true,
    duration: isFinite(d) ? d : 0,
    current: isFinite(c) ? c : 0,
    paused: !!v.paused,
    ended: !!v.ended,
    muted: !!v.muted,
    volume: Number(v.volume || 0),
    rate: Number(v.playbackRate || 1),
    readyState: Number(v.readyState || 0),
    title: norm(document.title).slice(0, 120),
    progressFlag: norm(v.closest && v.closest('[class]') ? (v.closest('[class]').className || '') : '').slice(0, 120)
  };
}
"""

# ------------------------------------------------------ 题目提取（练习题 / 考试题通用）
# 参数：cfg = {roots, stems, options, qnos, scores, correctFlags, wrongFlags,
#              answerText, userAnswerText, max}
# 返回：[{no, kind, kindName, stem, options[], my_answer, ref_answer, score, is_right,
#         has_image, top, text_len, source}]
EXTRACT_QUESTIONS_JS = r"""
(cfg) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const stripOpt = (s) => norm(s).replace(/^[A-Ha-h][\.、）\)]\s*/, '').replace(/^[（(][A-Ha-h][)）]\s*/, '');
  const LETTERS = 'ABCDEFGHIJKL';
  // 只处理“当前可见”的内容：学习通随堂弹题常预先埋在 DOM 里但 display:none，
  // 不过滤会在题目真正弹出前就误报。
  const visible = (el) => {
    try {
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) return false;
      const cs = window.getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') === 0) return false;
      let n = el;
      for (let i = 0; i < 6 && n; i++) {
        const s = window.getComputedStyle(n);
        if (s.display === 'none' || s.visibility === 'hidden') return false;
        n = n.parentElement;
      }
      return true;
    } catch (e) { return true; }
  };

  function pick(el, list) {
    for (const s of (list || [])) {
      try {
        const n = el.querySelector(s);
        if (n && norm(n.innerText)) return norm(n.innerText);
      } catch (e) { /* 无效选择器忽略 */ }
    }
    return '';
  }
  function pickEl(el, list) {
    for (const s of (list || [])) {
      try { const n = el.querySelector(s); if (n) return n; } catch (e) {}
    }
    return null;
  }
  function hasClass(el, key) {
    let n = el, d = 0;
    while (n && d < 5) {
      const cls = ((n.className || '') + ' ' + (n.id || '')).toLowerCase();
      if (cls.indexOf(key) >= 0) return true;
      n = n.parentElement; d++;
    }
    return false;
  }

  // ---------- 1. 找到题目容器 ----------
  function findRoots() {
    // 关键：必须**合并所有候选选择器**的结果。学习通随堂弹题常用另一套 class，
    // 若“第一个有结果的选择器”就 return，弹窗里的题永远扫不到。
    const merged = [];
    for (const s of (cfg.roots || [])) {
      let found = [];
      try {
        found = Array.prototype.slice.call(document.querySelectorAll(s));
      } catch (e) { continue; }
      found.filter((n) => norm(n.innerText).length > 8 && visible(n)).forEach((n) => {
        for (let k = merged.length - 1; k >= 0; k--) {
          const m = merged[k];
          if (m === n) { return; }
          if (m.contains(n)) { merged.splice(k, 1); }       // 取更内层的容器
          else if (n.contains(m)) { return; }               // 已有更内层的，跳过外层
        }
        merged.push(n);
      });
    }
    if (merged.length) return {roots: merged, by: 'union'};
    // 兜底 A：按 input 的 name 分组，向上找公共容器
    const groups = new Map();
    document.querySelectorAll('input,textarea,select').forEach((inp) => {
      if (['hidden', 'button', 'submit', 'reset'].indexOf((inp.type || '').toLowerCase()) >= 0) return;
      const key = norm(inp.getAttribute('name')) || norm(inp.getAttribute('id')) || '_';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(inp);
    });
    const roots = [];
    groups.forEach((inputs) => {
      let node = inputs[0].parentElement, depth = 0;
      while (node && depth < 8) {
        const own = norm(node.innerText).length;
        if (own > 15 && own < 6000 && visible(node)) { roots.push(node); break; }
        node = node.parentElement; depth++;
      }
    });
    if (roots.length) return {roots: dedupe(roots), by: 'input-group'};
    // 兜底 B：整页作为一个大容器（后续按题号切分）
    if (norm(document.body.innerText).length > 30) return {roots: [document.body], by: 'body'};
    return {roots: [], by: 'none'};
  }
  function dedupe(list) {
    const out = [];
    list.forEach((n) => { if (!out.some((m) => m === n || m.contains(n))) out.push(n); });
    return out;
  }

  // ---------- 2. 单题解析 ----------
  function optionTextFor(inp) {
    const id = inp.getAttribute('id');
    if (id) {
      const lab = document.querySelector('label[for="' + id + '"]');
      if (lab && norm(lab.innerText)) return norm(lab.innerText);
    }
    let n = inp.closest ? inp.closest('label') : null;
    if (n && norm(n.innerText)) return norm(n.innerText);
    n = inp.parentElement;
    for (let d = 0; d < 4 && n; d++) {
      const t = norm(n.innerText);
      if (t) return t;
      n = n.parentElement;
    }
    if (inp.nextElementSibling) return norm(inp.nextElementSibling.innerText || inp.nextElementSibling.textContent);
    return '';
  }

  function readOptions(root) {
    const opts = [];
    const keyed = new Map();
    root.querySelectorAll('input[type=radio],input[type=checkbox]').forEach((inp) => {
      if (!visible(inp)) return;
      const key = norm(inp.getAttribute('name')) || norm(inp.getAttribute('id')) || '_';
      if (!keyed.has(key)) keyed.set(key, []);
      const txt = optionTextFor(inp);
      keyed.get(key).push({
        text: stripOpt(txt), raw: txt, checked: !!inp.checked, value: inp.value || '',
        type: inp.type === 'radio' ? 'radio' : 'checkbox', id: inp.getAttribute('id') || '',
        name: inp.getAttribute('name') || ''
      });
    });
    keyed.forEach((list) => {
      if (!list.length) return;
      list.forEach((o, i) => {
        opts.push({
          label: LETTERS[i] || String(i), text: o.text || stripOpt(o.raw), raw: o.raw,
          checked: o.checked, value: o.value, id: o.id, input_type: o.type, name: o.name, nidx: i
        });
      });
    });
    if (!opts.length) {
      // 新版章节测验：选项是 li[role=radio/checkbox]（带 qid,点击 addChoice），没有原生 input
      const seenLi = new Set();
      root.querySelectorAll("li[role='radio'],li[role='checkbox'],li.before-after,ul.Zy_ulTop>li").forEach((li) => {
        if (seenLi.has(li)) return; seenLi.add(li);
        if (!visible(li)) return;
        let txt = norm(li.innerText).replace(/^[A-H]\s*[\.、）\)]?\s*/, '');
        if (!txt) return;
        const qid = li.getAttribute('qid') || '';
        const role = li.getAttribute('role') || '';
        const pos = (li.parentElement ? Array.prototype.indexOf.call(li.parentElement.children, li) + 1 : 1);
        // “已选”只认 aria-checked=true 或明确的选中态词；不能用 /check/ —— role=checkbox 的类名普遍含 “check” 会全误判
        const checked = li.getAttribute('aria-checked') === 'true'
          || /\b(cur|active|selected|chosen|choose|right)\b/i.test(li.className || '');
        opts.push({
          label: LETTERS[opts.length] || String(opts.length), text: txt, raw: li.innerText || txt,
          checked: !!checked, value: '', id: li.getAttribute('id') || '',
          input_type: role === 'checkbox' ? 'checkbox' : 'radio',
          name: qid ? 'qid#' + qid : '', nidx: -1,
          click_sel: qid ? ("li[qid='" + qid + "']:nth-child(" + pos + ")") : ''
        });
      });
    }
    if (!opts.length) {
      // 没有真实输入控件时，按“选项文本样式”识别（展示型题目/复习页）
      (cfg.options || []).forEach((s) => {
        try {
          Array.prototype.slice.call(root.querySelectorAll(s)).forEach((el, i) => {
            const t = stripOpt(el.innerText);
            if (t && t.length < 900) opts.push({label: LETTERS[i] || String(i), text: t, checked: false, value: '', id: '', input_type: 'text'});
          });
        } catch (e) {}
      });
    }
    return opts;
  }

  function readBlanks(root) {
    const out = [];
    root.querySelectorAll('textarea,input[type=text]').forEach((el) => {
      const val = norm(el.value);
      const wide = Number(el.offsetWidth || 0), high = Number(el.offsetHeight || 0);
      // 页面顶部“搜索”框也是 input[type=text]，必须带上尺寸与是否为题目容器内的作答框
      const inQuestion = !!(el.closest && el.closest('.TiMu,.questionLi,.question-item,.topic,.chapter_item,.ans-module'));
      if (inQuestion && (wide > 260 || high > 44 || val)) {
        out.push({value: val, ph: norm(el.getAttribute('placeholder')),
                  id: el.getAttribute('id') || '', wide: wide, high: high});
      }
    });
    return out;
  }

  function classify(root, stem, opts, blanks) {
    const all = norm(root.innerText).slice(0, 3000);
    const head = stem.slice(0, 120) + ' ' + all.slice(0, 160);
    const types = ['单选题', '多选题', '判断题', '填空题', '简答题', '论述题', '计算题', '名词解释', '判断题(对错题)'];
    let hint = '';
    for (const t of types) { if (head.indexOf(t) >= 0) { hint = t; break; } }
    if (hint.indexOf('判断') >= 0) return ['judge', hint];
    if (hint === '填空题') return ['blank', hint];
    if (['简答题', '论述题', '计算题', '名词解释'].indexOf(hint) >= 0) return ['short', hint];
    if (opts.length) {
      const kinds = opts.map((o) => o.input_type);
      if (kinds.indexOf('checkbox') >= 0) return ['multi', '多选题'];
      return ['single', '单选题'];
    }
    if (blanks.length) {
      const big = blanks.length === 1 && (blanks[0].value.length > 60);
      if (big) return ['short', '简答题'];
      return stem.indexOf('（）') >= 0 || stem.indexOf('___') >= 0 || /[\u005cs]{2,}/.test(stem) ? ['blank', '填空题'] : ['short', '简答题'];
    }
    return ['unknown', '未识别'];
  }

  function readFlag(root, list) {
    for (const s of (list || [])) {
      try { if (root.querySelector(s)) return true; } catch (e) {}
    }
    return false;
  }

  const found = findRoots();
  const list = (found.roots || []).slice(0, cfg.max || 300);
  const out = [];
  list.forEach((root, idx) => {
    const opts = readOptions(root);
    const blanks = readBlanks(root);
    let stem = pick(root, cfg.stems);
    const fullText = norm(root.innerText);
    if (!stem && fullText) {
      // 用第一个选项文本作为切分点，得到题干
      let cut = fullText;
      if (opts.length) {
        const first = (opts[0].raw || opts[0].text || '').slice(0, 14);
        if (first && fullText.indexOf(first) > 6) cut = fullText.slice(0, fullText.indexOf(first));
      }
      stem = cut.slice(0, 1200);
    }
    if (!stem || stem.length < 6) return;
    const kindInfo = classify(root, stem, opts, blanks);
    const noText = pick(root, cfg.qnos) || (stem.match(/^\s*(\d{1,3})\s*[\.、)）]/) ? RegExp.$1 : '') || String(idx + 1);
    const answerLetters = opts.filter((o) => o.checked).map((o) => o.label).join('');
    let myAnswer = answerLetters;
    if (!myAnswer && blanks.length) myAnswer = blanks.map((b) => b.value).filter(Boolean).join(' / ');
    out.push({
      index: idx,
      no: noText.replace(/^(\d{1,3})\s*[\.、)）]?\s*/, '$1'),
      kind: kindInfo[0],
      kind_name: kindInfo[1],
      stem: stem.slice(0, 4000),
      options: opts.map((o) => ({ label: o.label, text: o.text.slice(0, 1200), checked: o.checked, value: o.value, id: o.id, input_type: o.input_type, name: o.name, nidx: o.nidx, click_sel: o.click_sel || '' })),
      blanks: blanks,
      my_answer: myAnswer.slice(0, 4000),
      ref_answer: pick(root, cfg.answerText).slice(0, 2000),
      score: pick(root, cfg.scores).slice(0, 40),
      is_right: readFlag(root, cfg.correctFlags) ? 1 : (readFlag(root, cfg.wrongFlags) ? 0 : null),
      has_image: !!root.querySelector('img'),
      img_count: root.querySelectorAll('img').length,
      text_len: fullText.length,
      top: Math.round(root.getBoundingClientRect().top + window.scrollY),
      viewport_center_dist: Math.abs(root.getBoundingClientRect().top + root.getBoundingClientRect().height / 2 - window.innerHeight / 2),
      inView: (function () {
        const r = root.getBoundingClientRect();
        return r.width > 30 && r.height > 30 && r.bottom > 8 && r.top < window.innerHeight - 8 &&
               r.right > 8 && r.left < window.innerWidth - 8;
      })(),
      modal: !!(root.closest && root.closest('.vjs-overlay,.popWrap,#jwindow,.ans-videoquiz,.ans-timelineobjects,.tkTopic_oper,.tkTopic,[class*="modal"],[class*="dialog"],[class*="popup"],[class*="Popup"],[role="dialog"]')),
      detect_by: found.by
    });
  });
  return out;
}
"""

# ------------------------------------------------------ 页面正文分块（资料入库用）
COLLECT_TEXT_BLOCKS_JS = r"""
(limit) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const blocks = [];
  const push = (t, kind) => { t = norm(t); if (t.length > 4) blocks.push({text: t.slice(0, 4000), kind: kind || 'text'}); };
  document.querySelectorAll('h1,h2,h3,h4,p,li,td,th,pre,blockquote,figcaption').forEach((el) => {
    if (el.closest && el.closest('script,style,noscript')) return;
    const tag = el.tagName.toLowerCase();
    push(el.innerText, /^h\d$/.test(tag) ? 'heading' : 'text');
  });
  return blocks.slice(0, limit || 5000);
}
"""

# ------------------------------------------------------ 只读：当前视口中心的题目
CURRENT_VIEW_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  return {
    title: norm(document.title),
    url: location.href,
    scroll_y: Math.round(window.scrollY),
    view_h: window.innerHeight,
    body_len: norm(document.body ? document.body.innerText : '').length,
    visible_text: norm(document.body ? document.body.innerText : '').slice(0, 600)
  };
}
"""