# -*- coding: utf-8 -*-
"""
course.crawler —— 课程列表 / 章节目录 / 学习项识别

识别策略（按稳定性排序）：
1. URL 特征：courseId/cpi/clazzid、/knowledge/、/work/、/exam/、/doc/、ananas 等；
2. 文本特征：标题里的“视频/文档/PPT/测验/练习/作业”等关键词；
3. 选择器特征：集中管理的候选选择器（course/selectors.py）。
这样即使学习通改版换了 class 名，只要链接结构没变，程序仍然可用。

注意：本模块只做“读取”，不会调用任何修改服务器数据的接口。
"""

from __future__ import annotations

import hashlib
import re
import time as _time
from dataclasses import dataclass
from typing import Any, Iterable

from browser import actions as A
from browser.js import COLLECT_LINKS_JS
from course.models import Catalog, Chapter, Course, StudyItem
from course.selectors import KIND_KEYWORDS, SELECTORS, URL_PATTERNS, selectors
from utils.logger import get_logger
from utils.text import clean_text, one_line

log = get_logger("course.crawler")

# 章节标题（“第一章 …”“1.2 …”“单元三 …”“Module 2”）
CHAPTER_HEAD_RE = re.compile(
    r"^\s*(第\s*[0-9一二三四五六七八九十百]+\s*[章讲单元节]|Unit\s*\d+|Module\s*\d+|Lesson\s*\d+|"
    r"\d{1,2}(\.\d{1,2})?)\s*[:：、\.]?\s*(.{0,60})$",
    re.I,
)
# 页面里常见的“任务点完成状态”标记（只读识别）
CATALOG_STATUS_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  document.querySelectorAll('a,span,i,img,em,b').forEach((el) => {
    const cls = ((el.className || '') + ' ' + (el.id || '')).toString();
    const alt = norm(el.getAttribute && el.getAttribute('alt'));
    const title = norm(el.getAttribute && el.getAttribute('title'));
    const txt = norm(el.innerText).slice(0, 30);
    const sig = (cls + '|' + alt + '|' + title + '|' + txt).toLowerCase();
    let done = null;
    if (/unlearn|notlearn|weiwancheng|未学|未完成|not\s*done|task-undone/.test(sig)) done = 0;
    else if (/learned|haslearn|yiwancheng|已学|已完成|finished|complete|done/.test(sig)) done = 1;
    if (done !== null) {
      const host = el.closest('li,tr,div');
      const link = host ? host.querySelector('a[href]') : null;
      out.push({done: done, text: norm(host ? host.innerText : '').slice(0, 120),
                url: link ? link.href : '', top: Math.round((host || el).getBoundingClientRect().top + window.scrollY)});
    }
  });
  return out.slice(0, 800);
}
"""


def _sha(text: str, size: int = 16) -> str:
    return hashlib.sha1(str(text).encode("utf-8", "ignore")).hexdigest()[:size]


def _query(url: str, *names: str) -> dict[str, str]:
    from urllib.parse import parse_qs, urlparse

    try:
        qs = parse_qs(urlparse(url).query, keep_blank_values=True)
    except Exception:
        return {n: "" for n in names}
    lower = {k.lower(): v for k, v in qs.items()}   # 学习通各入口大小写不一(courseId/clazzId/courseid…)
    return {n: (lower.get(n.lower(), [""])[0] if lower else "") for n in names}



SPACE_CARDS_JS = r"""
(limit) => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const skip = new Set(['首页', '笔记', '云盘', '收件箱', '退出登录', '进入空间', '账号管理',
                        '切换单位/角色', '下载客户端', '更多', '搜索', '课程门户', '章节目录',
                        '讨论', '作业', '考试', '资料', '章节', '学校', '学习记录', '错题集',
                        '课程图谱', '新生自选课', 'AI工具', '最新通知', '待办事项']);
  const seen = new Map();
  document.querySelectorAll('[aria-label],[title]').forEach((el) => {
    const label = norm(el.getAttribute('aria-label') || el.getAttribute('title'));
    if (!label || label.length < 4 || label.length > 40) return;
    if (skip.has(label)) return;
    if (/^(【?\\d{4}[秋季春夏]?】?){0,1}$/.test(label)) return;
    if (/通知|提示|下载|客户端|邀请码|应用|工具|入口|设置|帮助/.test(label)) return;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return;
    if (!seen.has(label)) {
      seen.set(label, { label: label, top: Math.round(r.top + window.scrollY) });
    }
  });
  return Array.from(seen.values()).slice(0, limit || 14);
}
"""

COURSE_URL_JS = r"""
() => {
  const urls = [];
  const push = (u) => { if (u) urls.push(String(u)); };
  push(location.href);
  document.querySelectorAll('a[href]').forEach((a) => push(a.href));
  document.querySelectorAll('[onclick],[data],[data-url]').forEach((el) => {
    const raw = (el.getAttribute('onclick') || '') + ' ' + (el.getAttribute('data') || '') +
                ' ' + (el.getAttribute('data-url') || '');
    const m = raw.match(/https?:\\/\\/[^"'\\s)]+/g);
    if (m) m.forEach(push);
  });
  return Array.from(new Set(urls)).slice(0, 400);
}
"""

FANYA_CATALOG_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const argsOf = (raw) => {
    const i = raw.indexOf('(');
    const j = raw.indexOf(')', i + 1);
    if (i < 0 || j < 0) return [];
    return raw.slice(i + 1, j).split(',').map((x) => x.trim().replace(/["']/g, ''));
  };
  const topOf = (el) => { try { return Math.round(el.getBoundingClientRect().top + window.scrollY); } catch (e) { return 0; } };
  const parseItem = (el, unitIndex) => {
    const oc = el.getAttribute('onclick') || '';
    const a = argsOf(oc);
    const idAttr = (el.getAttribute('id') || '');
    const jobId = a[1] || (idAttr.indexOf('cur') === 0 ? idAttr.slice(3) : '');
    if (!jobId) return null;
    const text = norm(el.innerText);
    const pend = text.match(/(\d+)\s*个?待完成任务点/);
    return {
      courseId: a[0] || '', jobId: jobId, clazzid: a[2] || '',
      title: norm(el.getAttribute('title')) || text.slice(0, 80),
      fullText: text.slice(0, 220),
      pending: pend ? pend[1] : '',
      unitIndex: unitIndex,
      top: topOf(el)
    };
  };

  const units = Array.prototype.slice.call(document.querySelectorAll('.chapter_unit'));
  const chapters = [];
  const sections = [];
  units.forEach((u, ui) => {
    let head = '';
    const titles = Array.prototype.slice.call(u.querySelectorAll('.catalog_title'));
    for (const el of titles) {
      const s = norm(el.innerText);
      if (s && s.indexOf('待完成任务点') < 0 && s.length <= 60) { head = s; break; }
    }
    if (!head) {
      const p = u.querySelector('p,h2,h3,.chapter_name,.catalog_name');
      head = norm(p ? p.innerText : '').slice(0, 60);
    }
    chapters.push({ text: head, top: topOf(u), unitIndex: ui });
    Array.prototype.slice.call(u.querySelectorAll('.chapter_item')).forEach((el) => {
      const s = parseItem(el, ui);
      if (s) sections.push(s);
    });
  });

  if (!sections.length) {
    // 兜底：全局扫描任何带 toOld 的元素（学习通该处理器挂在 div 上，不是 a）
    Array.prototype.slice.call(document.querySelectorAll('[onclick]')).forEach((el) => {
      if ((el.getAttribute('onclick') || '').indexOf('toOld') < 0) return;
      const s = parseItem(el, -1);
      if (s) sections.push(s);
    });
    if (!chapters.length) {
      Array.prototype.slice.call(document.querySelectorAll('.catalog_title')).forEach((el, i) => {
        const s = norm(el.innerText);
        if (s && s.indexOf('待完成任务点') < 0 && s.length <= 60) chapters.push({ text: s, top: topOf(el), unitIndex: i });
      });
    }
  }
  const head = document.querySelector('.chapter_head') || document.querySelector('.catalog_ressbar');
  return { sections: sections, chapters: chapters, unitCount: units.length,
           summary: norm(head ? head.innerText : '').slice(0, 200) };
}
"""

CHAPTER_TAB_TEXTS = ("章节", "章节目录", "课程章节", "章节任务", "目录")
CHAPTER_TAB_JS = r"""
(texts) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const want = new Set(texts);
  const nodes = Array.prototype.slice.call(document.querySelectorAll('a,span,li,div,button,em,h3,h4'));
  for (const el of nodes) {
    const own = norm(el.innerText).slice(0, 12);
    if (!want.has(own)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    el.scrollIntoView({block: 'center'});
    el.click();
    return {ok: true, text: own, tag: el.tagName};
  }
  return {ok: false};
}
"""

GRADE_ROWS_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const cfg = window.__GRADE_SELS__ || {};
  const sels = (k, d) => (cfg[k] && cfg[k].length ? cfg[k] : d);
  const rows = [];
  document.querySelectorAll(sels('grade_rows', ['table tr','.grade-list li','.scorelist li','ul.list li','.el-table__row','div[class*=grade] li']))
    .forEach((el) => {
      const text = norm(el.innerText).slice(0, 200);
      if (!text) return;
      const nameEl = el.querySelector(sels('grade_row_name', ['td:first-child','.name','a','span']));
      const name = norm(nameEl ? nameEl.innerText : '');
      if (!/[一-龥A-Za-z]/.test(name || text)) return;
      const r = el.getBoundingClientRect();
      rows.push({name: name || text.slice(0,24), text, top: Math.round(r.top + window.scrollY)});
    });
  const ov = document.querySelector(sels('grade_overview', ["[class*=totalScore]",'.score_total',":text('总评')"]));
  return {rows: rows.slice(0, 400), overview: norm(ov ? ov.innerText : '')};
}
"""

# “学习记录/综合成绩”页(studstat2-ans.chaoxing.com/study-data/index)按稳定 id 抽数,
# 任一字段命中即认为是该页(避免与通用成绩表混淆)。
STUDY_SCORE_JS = r"""
() => {
  const txt = (id) => { const el = document.getElementById(id); return el ? (el.innerText||'').trim() : null; };
  const pick = (sel) => { const el = document.querySelector(sel); return el ? (el.innerText||'').trim() : null; };
  const score = pick('.userScore h3 span') || pick('.userScore span');
  const d = {
    score: score,
    jobfinish: txt('jobfinish'), jobpublish: txt('jobPublish'),
    jobper: txt('jobPer'), jobrank: txt('jobRank'),
    point: txt('point'),
    sign: {attendance: txt('attendanceCount'), absence: txt('realAbsenceCount'),
           late: txt('lateCount'), early: txt('earlyCount'), overdue: txt('overdueCount')},
  };
  const has = d.score || d.jobfinish || d.point || d.sign.attendance;
  return has ? d : null;
}
"""

# “综合成绩”页:读“查看考核标准”各 module(标题含权重% + 得分规则)与已渲染的成绩表行。
# 注:考核标准是隐藏弹层,innerText 取空,故用 textContent。
OVERALL_JS = r"""
() => {
  const tx = (el) => el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : '';
  const modules = [];
  document.querySelectorAll('.decDialog .module').forEach((m) => {
    const h = m.querySelector('h2'), p = m.querySelector('p');
    const title = tx(h);
    if (title) modules.push({title: title, rule: tx(p)});
  });
  const table = [];
  const HEAD = /^(权重名称|权重占比|得分说明|所占权重|我的成绩|操作|序号|名称|成绩)$/;
  document.querySelectorAll('.layui-table-body table tr, #myTable tr').forEach((tr) => {
    const cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(tx).filter(Boolean);
    if (cells.length >= 2 && !HEAD.test(cells[0])) table.push({name: cells[0], text: cells.join(' ')});
  });
  return (modules.length || table.length) ? {modules, table} : null;
}
"""


@dataclass
class CourseCrawler:
    browser: Any
    cfg: Any
    store: Any

    # ------------------------------------------------------------ 课程列表
    def sync_courses(self) -> list[Course]:
        """读取“我的课程”列表并写入本地库（合并多个入口，尽量读全）。"""
        page = self.browser.start().page
        assert page is not None
        seen_urls: list[str] = []
        for url in (self.cfg.get("COURSE_LIST_URL"),
                    "https://fycourse.fanya.chaoxing.com/fyportal/courselist/course",
                    self.cfg.get("CHAOXING_HOME_URL"),
                    f"{(self.cfg.get('CHAOXING_HOME_URL') or 'https://i.chaoxing.com').rstrip('/')}/base"):
            if url and url not in seen_urls:
                seen_urls.append(url)
        found: dict[str, Course] = {}

        def merge(cs: Iterable[Course]) -> None:
            for course in cs:
                if not course.course_key:
                    continue
                prev = found.get(course.course_key)
                if prev is None:
                    found[course.course_key] = course
                else:                              # 合并更完整的信息（教师/班级/地址）
                    prev.teacher = prev.teacher or course.teacher
                    prev.class_name = prev.class_name or course.class_name
                    prev.progress_text = prev.progress_text or course.progress_text
                    if (not prev.url or "courseId" not in prev.url) and "courseId" in (course.url or ""):
                        prev.url = course.url

        for url in seen_urls:
            if not A.safe_goto(page, url, attempts=int(self.cfg.get("MAX_RETRY_PER_ITEM") or 3)):
                continue
            A.close_popups(page)
            self._scroll_to_load_all(page)         # 课程网格常是懒加载，先滚一遍
            merge(self._courses_from_page(page))
        if len(found) < 3:
            # 点开式发现兜底（个人空间“我学的课”是 Vue 渲染的 div，没有可用链接）
            try:
                merge(self.discover_courses_by_clicking(page, limit=12))
            except Exception as exc:
                log.debug("点开式课程发现失败：%s", exc)
        if not found:
            log.warning("未能在课程页解析到课程，已导出页面结构供排查")
            self.browser.dump_html(page, "course_list_fail")
        for c in found.values():
            cid = self.store.upsert_course(c.to_dict())
            c.id = cid
        courses = sorted(found.values(), key=lambda x: x.name)
        log.info("课程同步完成：%s 门", len(courses))
        return courses

    def _scroll_to_load_all(self, page: Any, max_rounds: int = 8) -> None:
        """向下滚动触发懒加载，直到高度不再增长或达到上限（只读，不改数据）。"""
        import time as _t

        js = r"""
        () => {
          window.scrollTo(0, document.body.scrollHeight);
          return document.body.scrollHeight;
        }
        """
        last = -1
        for _ in range(max_rounds):
            h = A.js_eval(page, js)
            try:
                h = int(h or 0)
            except (TypeError, ValueError):
                break
            if h == last:
                break
            last = h
            _t.sleep(0.8)
        A.js_eval(page, "() => window.scrollTo(0, 0)")
        _t.sleep(0.4)

    def _courses_from_page(self, page: Any) -> list[Course]:
        out: dict[str, Course] = {}
        patterns = URL_PATTERNS["course_home"] + ["mycourse", "/course/"]
        # 课程卡片可能在内嵌 iframe（个人空间/mooc2 常如此），逐 frame 收集才不漏读
        for frame in A.frames_of(page):
            rows = A.js_eval(frame, COLLECT_LINKS_JS,
                             {"groupSelectors": selectors("catalog_group_title")}) or []
            for row in rows:
                url = row.get("url", "")
                if "chaoxing.com" not in url:
                    continue
                ids = _query(url, "courseId", "cpi", "clazzid", "enc", "id")
                cid = ids.get("courseId") or ids.get("id") or ""
                if not cid:
                    continue
                if not any(p in url for p in patterns):
                    continue
                name = one_line(row.get("text", ""))[:60]
                if not name or name in ("开始学习", "课程", "进入课程"):
                    # 链接文字是按钮时，向父级卡片取标题
                    name = self._course_name_nearby(frame, url) or name
                key = Course.make_key(cid, ids.get("cpi", ""), ids.get("clazzid", ""))
                course = out.get(key)
                if course is None:
                    course = Course(
                        course_key=key, name=clean_text(name) or f"课程{cid}", url=url,
                        cpi=ids.get("cpi", ""), clazzid=ids.get("clazzid", ""),
                    )
                    out[key] = course
                elif (not course.url or "courseId" not in course.url) and "courseId" in url:
                    course.url = url      # 用更“实”的课程地址覆盖占位地址
                if clean_text(name) and (not course.name or course.name.startswith("课程")):
                    course.name = clean_text(name)[:60]   # 封面链接空文字先建档时，用带真名的标题链接回填
            # 卡片标题/教师/进度补充（同一 frame 内才有对应元素）
            for course in out.values():
                card = self._course_card_info(frame, course)
                if card.get("name"):
                    course.name = card["name"]
                course.teacher = card.get("teacher", "") or course.teacher
                course.class_name = card.get("class_name", "") or course.class_name
                course.progress_text = card.get("progress_text", "") or course.progress_text
        return list(out.values())

    def _course_name_nearby(self, frame: Any, url: str) -> str:
        js = r"""
        (target) => {
          const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
          const links = Array.from(document.querySelectorAll('a[href]'));
          for (const a of links) {
            if (a.href !== target) continue;
            let n = a, d = 0;
            while (n && d < 6) {
              for (const s of ['.course-name', 'p.course-name', '.courseName', 'h3', '.tit', '[title]']) {
                const t = n.querySelector && n.querySelector(s);
                if (t && norm(t.innerText || t.getAttribute('title')).length > 1) {
                  return norm(t.getAttribute('title') || t.innerText).slice(0, 60);
                }
              }
              n = n.parentElement; d++;
            }
          }
          return '';
        }
        """
        return one_line(A.js_eval(frame, js, url) or "")

    def _course_card_info(self, frame: Any, course: Course) -> dict[str, str]:
        js = r"""
        (key) => {
          const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
          const cards = Array.from(document.querySelectorAll('li,tr,div'));
          for (const c of cards) {
            const link = c.querySelector("a[href*='courseId']");
            if (!link) continue;
            const m = (link.href || '').match(/courseId=([^&]+)/);
            if (!m || (m[1] + '|' + (link.href.match(/cpi=([^&]*)/)||[])[1]) !== key) continue;
            const name = c.querySelector('.course-name,p.course-name,.courseName,h3,.tit,[title]');
            const teacher = c.querySelector('.teacher,p.course-teacher,.info__teacher');
            const clazz = c.querySelector('.course-info__term,.term,.term-name,[class*=clazz],.className,.course-class');
            const prog = c.querySelector('.progress,.process,[class*=schedule]');
            return {
              name: norm(name ? (name.getAttribute('title') || name.innerText) : '').slice(0, 60),
              teacher: norm(teacher ? teacher.innerText : '').slice(0, 40),
              class_name: norm(clazz ? clazz.innerText : '').slice(0, 40),
              progress_text: norm(prog ? prog.innerText : '').slice(0, 40)
            };
          }
          return {};
        }
        """
        cid = course.course_key.split("_")[0]
        cpi = course.cpi
        return dict(A.js_eval(frame, js, f"{cid}|{cpi}") or {})

    # ------------------------------------------------------------ 章节目录
    def _chapters_from_fanya(self, cat: dict[str, Any], course: Course):
        """把章节页数据整理成 Chapter/StudyItem（按 chapter_unit 归属，精确不猜）。"""
        sections = cat.get("sections") or []
        units = cat.get("chapters") or []
        chapters: list[Chapter] = []
        by_unit: dict[int, Chapter] = {}
        for u in units:
            text = one_line(u.get("text", ""))
            if not text:
                continue
            ui = int(u.get("unitIndex", len(chapters)))
            if ui in by_unit:
                continue
            m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(.*)$", text)
            no = m.group(1) if m else ""
            title = (m.group(2) if m else text) or text
            ch = Chapter(chap_key=f"ch_{ui + 1}_{_sha(text, 6)}", title=clean_text(title)[:60],
                         no=no, level=1, order_idx=ui)
            by_unit[ui] = ch
            chapters.append(ch)
        chapters.sort(key=lambda c: c.order_idx)
        if not chapters:
            chapters.append(Chapter(chap_key="ch_1", title=course.name or "全部章节", order_idx=0))

        items: list[StudyItem] = []
        for idx, s in enumerate(sections):
            full = one_line(s.get("fullText", ""))
            title = one_line(s.get("title", "")) or full[:60] or f"小节{idx + 1}"
            m = re.match(r"^\s*(\d+\.\d+)\s*", full)
            no = m.group(1) if m else ""
            pending = str(s.get("pending") or "").strip()
            # 完成状态只在“有正向证据”时才为真：
            #   · 明确写着 0 个待完成任务点；或
            #   · 文本里出现“已完成/已学完”标记。
            # 空白或未渲染一律按未完成处理（宁可重复学习，也不伪造完成）。
            if pending.isdigit():
                done = int(pending) == 0
            else:
                done = ("已完成" in full) or ("已学完" in full)
            unknown = (not pending) and ("待完成" not in full) and not done
            ui = int(s.get("unitIndex", -1))
            ch = by_unit.get(ui)
            if ch is None and chapters:            # 没有 unit 信息时按序号兜底
                ch = chapters[min(len(chapters) - 1, idx // max(1, (len(sections) // max(1, len(chapters)))))]
            item = StudyItem(
                item_key=_sha(str(s.get("jobId") or title), 20),
                title=(f"{no} {title}".strip() if no and not title.startswith(no) else title)[:120],
                kind="other",
                url=f"fanya://toOld/{s.get('courseId')}/{s.get('jobId')}/{s.get('clazzid')}",
                chapter_key=ch.chap_key if ch else "", order_idx=idx,
                done=bool(done), platform_done=(False if unknown else bool(done)),
            )
            items.append(item)
        return chapters, items


    def parse_fanya_catalog(self, page) -> dict[str, Any]:
        """解析学习通“章节”页（小节靠 toOld() 跳转，没有 href）。"""
        data: dict[str, Any] = {}
        for frame in A.frames_of(page):
            got = A.js_eval(frame, FANYA_CATALOG_JS)
            if got and got.get("sections"):
                data = got
                break
        return data

    def open_item(self, page, item: dict[str, Any], catalog_url: str = "") -> bool:
        """
        打开一个学习项：
          * 普通 http 地址 → 直接跳转；
          * fanya://toOld/... → 先回到章节目录页，再调用页面自身的 toOld()（等价于你点这一节）。
        """
        import time as _t

        url = str(item.get("url") or "")
        if not url.startswith("fanya://"):
            return A.safe_goto(page, url, attempts=int(self.cfg.get("MAX_RETRY_PER_ITEM") or 3))
        # 形如 fanya://toOld/<courseId>/<jobId>/<clazzId> → split('/') 得到 6 段
        parts = url.split("/")
        if len(parts) < 6:
            log.warning("小节标识异常：%s", url[:60])
            return False
        cid, job_id, clazzid = parts[-3], parts[-2], parts[-1]
        if not job_id:
            log.warning("小节 jobId 为空：%s", url[:60])
            return False
        target = catalog_url or str(self.store.get_meta(f"catalog_url:{item.get('course_id','')}", "") or "")
        # 方式一：直接真实点击该小节元素（toOld 处理器挂在 div.chapter_item 上）
        for frame in A.frames_of(page):
            try:
                loc = frame.locator(f"div.chapter_item[id='cur{job_id}'], .chapter_item[onclick*='{job_id}']").first
                if loc.count() == 0:
                    continue
                loc.scroll_into_view_if_needed(timeout=3000)
                loc.click(timeout=4000)
                _t.sleep(3)
                A.close_popups(page)
                return True
            except Exception as exc:
                log.debug("点击小节失败，改用 toOld()：%s", exc)
        # 方式二：调用页面自己的 toOld() 函数
        for attempt in range(2):
            if target and "studentcourse" not in str(page.url or ""):
                A.safe_goto(page, target, attempts=2)
                _t.sleep(2)
            if not self._looks_like_catalog([]) and not A.js_eval(
                    page, "() => typeof window.toOld === 'function'"):
                for frame in A.frames_of(page):
                    if A.js_eval(frame, "() => typeof window.toOld === 'function'"):
                        break
            for frame in A.frames_of(page):
                if not A.js_eval(frame, "() => typeof window.toOld === 'function'"):
                    continue
                res = A.js_eval(frame, """(args) => {
                    try { window.toOld(args[0], args[1], args[2], 0); return 'called'; }
                    catch (e) { return 'err:' + e; }
                }""", [cid, job_id, clazzid])
                if res == "called":
                    _t.sleep(3)
                    A.close_popups(page)
                    return True
            _t.sleep(2)
        log.warning("无法打开小节：%s", str(item.get("title"))[:40])
        return False

    def open_section(self, page, course: dict[str, Any], job_id: str) -> bool:
        """
        打开某个小节：优先直接跳 URL；没有 URL 时在章节页里调用页面自己的 toOld()
        （等价于你在页面上点一下这一节），不构造任何请求。
        """
        import time as _t

        if job_id and not str(job_id).startswith("http"):
            for frame in A.frames_of(page):
                has = A.js_eval(frame, "() => typeof window.toOld === 'function'")
                if not has:
                    continue
                res = A.js_eval(frame, """(args) => {
                    try { window.toOld(args[0], args[1], args[2], 0); return 'called'; }
                    catch (e) { return 'err:' + e; }
                }""", [course.get("courseid", ""), job_id, course.get("clazzid", "")])
                if isinstance(res, str) and res == "called":
                    _t.sleep(3)
                    return True
        return False


    def open_section_task_tab(self, page) -> bool:
        """在小节页里点“章节检测/章节测验”切换标签，让题目显示出来（导航类点击，不是作答）。

        学习通把标签名放在 <li title="章节测验"> 的 title 属性里（视频是 title="视频"），
        可见文本可能是“2 章节测验”或被拆分，所以优先按 title 属性匹配，再退回文本/类名。
        """
        import time as _t

        from course.selectors import selectors
        words = [str(w) for w in selectors("section_task_tab") if str(w).strip()]
        templates = ('li[title="{w}"]', 'li[title*="{w}"]', '[onclick][title*="{w}"]',
                     '.clicktitle:has-text("{w}")', 'li:has-text("{w}")')
        for w in words:
            for tpl in templates:
                css = tpl.format(w=w)
                for frame in A.frames_of(page):
                    try:
                        loc = frame.locator(css)
                        if loc.count() == 0:
                            continue
                        loc.first.click(timeout=2500, force=True)
                        _t.sleep(2)
                        A.close_popups(page)
                        log.info("已点开“%s”入口（选择器：%s）", w, css)
                        return True
                    except Exception as exc:
                        log.debug("点开“%s”（%s）失败：%s", w, css, exc)
        log.warning("未找到“章节检测/测验”入口标签")
        return False

    def section_unfinished_count(self, page) -> int | None:
        """读当前小节“待完成任务点数”(input.jobUnfinishCount)。0=全部完成；读不到返回 None。"""
        js = ("()=>{const i=document.querySelector('input.jobUnfinishCount, .jobUnfinishCount');"
              "if(!i)return null;const v=(i.value || i.getAttribute('value') || '').trim();"
              "const n=parseInt(v,10);return isNaN(n)?null:n;}")
        for frame in A.frames_of(page):
            r = A.js_eval(frame, js)
            if r is not None:
                try:
                    return int(r)
                except Exception:
                    continue
        return None

    def section_task_pending(self, page) -> bool | None:
        """进入章节测验视图后判断：True=待完成可作答，False=已完成，None=看不出来。"""
        js = ("()=>{const t=(document.body&&document.body.innerText)||'';"
              "if(t.includes('待完成'))return true;"
              "if(/你的成绩|得分|已完成/.test(t))return false;return null;}")
        for frame in A.frames_of(page):
            r = A.js_eval(frame, js)
            if r is True:
                return True
            if r is False:
                return False
        return None

    def sync_catalog(self, course: Course) -> Catalog:
        """读取章节目录，识别视频/文档/PPT/章节测验/练习题，并写入本地库。"""
        page = self.browser.start().page
        assert page is not None
        url = course.url or self.cfg.get("COURSE_LIST_URL")
        if not A.safe_goto(page, url, attempts=int(self.cfg.get("MAX_RETRY_PER_ITEM") or 3)):
            return Catalog(course=course)
        A.close_popups(page)
        rows = self._collect_rows(page)
        # 课程门户页只有“资料/作业/考试/讨论”等分类入口，需要进入“章节”Tab 才有真正目录。
        # 规则：谁的“小节链接(/knowledge/ 或 jobid)”多就用谁；都抓不到时退回原页面。
        def score_of(rs):
            return (self._knowledge_count(rs), self._section_count(rs))

        best, best_score = rows, score_of(rows)
        if not self._looks_like_catalog(rows):
            for cand in self._catalog_tab_urls(page):
                if not A.safe_goto(page, cand, attempts=1):
                    continue
                got = self._collect_rows(page)
                if score_of(got) > best_score:
                    best, best_score = got, score_of(got)
                if self._looks_like_catalog(best):
                    break
            rows = best
        if not self._looks_like_catalog(rows):
            # 章节标签常是 SPA 跳转（href=javascript:void(0)）：直接点一下再抓，
            # 并轮询等待小节渲染（学习通该页是异步加载，等太短会抓到空壳）。
            A.safe_goto(page, url, attempts=1)
            if self._open_chapter_tab(page):
                for _ in range(10):
                    got = self._collect_rows(page)
                    if self.parse_fanya_catalog(page).get("sections"):
                        break
                    _time.sleep(2.5)
                if score_of(got) > score_of(rows):
                    rows = got
        # 学习通很多课程的小节没有 href，而是 onclick=toOld(courseId, jobId, clazzid, 0)
        cat = self.parse_fanya_catalog(page)
        if not cat.get("sections"):
            for _ in range(4):          # 再给异步渲染一点时间
                _time.sleep(2.0)
                cat = self.parse_fanya_catalog(page)
                if cat.get("sections"):
                    break
        if cat.get("sections"):
            info = self.store if self.store else None
            course.id = course.id or (self.store.upsert_course(course.to_dict()) if self.store else None)
            chapters, items = self._chapters_from_fanya(cat, course)
            if info is not None:
                self.store.replace_chapters(int(course.id), [c.to_dict() for c in chapters])
                for it in items:
                    row = self.store.upsert_item(int(course.id), it.to_dict())
                    it.id = row["id"]
                    if it.platform_done and not row["done"]:
                        self.store.finish_item(row["id"], "章节页标注已完成")
            try:
                self.store.set_meta(f"catalog_url:{course.id}", str(page.url or ""))
            except Exception:
                pass
            log.info("章节页解析：%s 章 / %s 小节（%s）", len(chapters), len(items), cat.get("summary", "")[:40])
            return Catalog(course=course, chapters=chapters, items=items)
        if not self._looks_like_catalog(rows):
            log.warning("仍未找到带小节链接的章节目录页，当前抓到的是课程门户分类入口")
            self.browser.dump_html(page, "catalog_no_section")
        statuses = A.js_eval(page, CATALOG_STATUS_JS) or []
        status_index = {one_line(s.get("text", ""))[:40]: s.get("done") for s in statuses if s.get("text")}
        chapters: list[Chapter] = []
        items: list[StudyItem] = []
        current: Chapter | None = None
        order = 0
        for row in sorted(rows, key=lambda r: (r.get("top", 0), r.get("text", ""))):
            text = one_line(row.get("text", ""))
            link = row.get("url", "")
            if not text and not link:
                continue
            if self._is_chapter_head(text, link, bool(row.get("group"))):
                current = self._make_chapter(text, current, len(chapters))
                if current.chap_key not in [c.chap_key for c in chapters]:
                    chapters.append(current)
                continue
            kind = self.classify_kind(link, text)
            if kind is None:
                continue
            item = StudyItem(
                item_key=_sha(one_line(link) or text, 20), title=text[:120] or f"{kind}任务",
                kind=kind, url=link, chapter_key=current.chap_key if current else "", order_idx=order,
            )
            item.platform_done = self._lookup_status(text, status_index)
            item.done = bool(item.platform_done)
            items.append(item)
            order += 1
        # 若页面没有明显“章”标题，则建一个默认章，避免条目无处归
        if not chapters:
            chapters.append(Chapter(chap_key="ch_default", title=course.name or "全部章节", no="", level=1))
            for it in items:
                it.chapter_key = it.chapter_key or "ch_default"
        course.id = course.id or self.store.upsert_course(course.to_dict())
        self.store.replace_chapters(course.id, [c.to_dict() for c in chapters])
        saved = 0
        for it in items:
            row = self.store.upsert_item(course.id, it.to_dict())
            it.id = row["id"]
            if it.platform_done is not None and not it.done and int(it.platform_done) == 1:
                self.store.finish_item(row["id"], "平台标注已完成")
                it.done = True
            saved += 1
        log.info("目录同步：%s 个章节 / %s 个学习项", len(chapters), saved)
        return Catalog(course=course, chapters=chapters, items=items)

    def _collect_rows(self, page: Any) -> list[dict[str, Any]]:
        return list(A.js_eval(page, COLLECT_LINKS_JS,
                              {"groupSelectors": selectors("catalog_group_title")}) or [])

    def _section_count(self, rows: list[dict[str, Any]]) -> int:
        return sum(1 for r in rows if self.classify_kind(r.get("url", ""), r.get("text", "")))

    def _open_nav_tab(self, page, texts: tuple[str, ...]) -> bool:
        """通用：点击课程门户顶部的标签（章节/作业/考试/资料…）。"""
        import time as _t

        for frame in A.frames_of(page):
            for txt in texts:
                try:
                    loc = frame.get_by_text(txt, exact=True).first
                    if loc.count() == 0:
                        continue
                    loc.scroll_into_view_if_needed(timeout=2500)
                    loc.click(timeout=3500)
                    _t.sleep(3.5)
                    log.info("已点击“%s”标签（frame: %s）", txt, str(frame.url)[:60])
                    return True
                except Exception:
                    continue
        for frame in A.frames_of(page):
            try:
                info = frame.evaluate(CHAPTER_TAB_JS, list(texts))
            except Exception:
                info = None
            if info and info.get("ok"):
                _t.sleep(3.5)
                log.info("已用 JS 点击“%s”标签", info.get("text"))
                return True
        return False

    def _open_chapter_tab(self, page) -> bool:
        """点课程页的“章节”标签（学习通该标签常是 SPA 跳转，没有可用 href）。"""
        import time as _t

        for frame in A.frames_of(page):
            for txt in CHAPTER_TAB_TEXTS:
                try:
                    loc = frame.get_by_text(txt, exact=True).first
                    if loc.count() == 0:
                        continue
                    loc.scroll_into_view_if_needed(timeout=2500)
                    loc.click(timeout=3500)          # 真实鼠标事件，Vue 组件才响应
                    _t.sleep(3.5)
                    log.info("已点击“%s”标签（frame: %s）", txt, str(frame.url)[:60])
                    return True
                except Exception:
                    continue
        for frame in A.frames_of(page):
            try:
                info = frame.evaluate(CHAPTER_TAB_JS, list(CHAPTER_TAB_TEXTS))
            except Exception:
                info = None
            if info and info.get("ok"):
                _t.sleep(3.5)
                log.info("已用 JS 点击“%s”标签", info.get("text"))
                return True
        return False


    def fetch_grades(self, page: Any, course: dict[str, Any]) -> dict[str, Any]:
        """只读抓取课程成绩:进课程→点“成绩”标签→收原始行→纯解析→整批落库。"""
        from course.grades import build_study_rows, parse_grade_rows, study_overview_text

        dest = course.get("url") or self.store.get_meta(f"course_url:{course['id']}", "")
        if dest:
            A.safe_goto(page, dest, attempts=2)
        A.close_popups(page)
        # 学生端“成绩”多在“学习记录”栏(部分课才有独立“成绩”标签);逐个尝试点入。
        if not self._open_nav_tab(page, ("学习记录", "成绩", "学习分析", "我的成绩", "成绩查询")):
            log.warning("未找到“学习记录/成绩”入口,尝试直接在当前页解析")
        # 把选择器注入页面(与 SELECTOR_OVERRIDES 合并后的结果一致)
        try:
            page.evaluate(
                "(sel) => { window.__GRADE_SELS__ = sel; }",
                {k: selectors(k) for k in ("grade_rows", "grade_row_name", "grade_overview")},
            )
        except Exception:
            log.debug("注入成绩选择器失败,使用内置候选", exc_info=True)
        rows, overview = [], ""
        # 优先解析“学习记录/综合成绩”页(稳定 id);命中即采用。
        for frame in [page, *A.frames_of(page)]:
            try:
                study = A.js_eval(frame, STUDY_SCORE_JS)
            except Exception:
                study = None
            if study:
                rows = build_study_rows(study)
                overview = study_overview_text(study)
                break
        # 没有学习记录结构时,回退到通用成绩表解析。
        if not rows:
            info = {"rows": [], "overview": ""}
            for frame in [page, *A.frames_of(page)]:
                try:
                    got = A.js_eval(frame, GRADE_ROWS_JS)
                except Exception:
                    continue
                if got and got.get("rows"):
                    info = got
                    break
            rows = parse_grade_rows(info.get("rows") or [])
            overview = (info.get("overview") or "").strip()
        # 抽到概览后,点进“综合成绩”补“考核权重/讨论规则/各模块实得分”。
        if rows:
            try:
                opened = self._open_nav_tab(page, ("综合成绩",))
            except Exception:
                opened = False
            try:
                detail = self._fetch_overall_detail(page, opened)
                if detail:
                    from course.grades import merge_grade_rows
                    rows = merge_grade_rows(rows, detail)
            except Exception:
                log.debug("读取“综合成绩”明细失败", exc_info=True)
        if not rows:
            # 抓空多半是选择器没命中/成绩在 iframe 或子页：自动导出页面结构，便于在
            # user_config.SELECTOR_OVERRIDES 里补 grade_* 候选，无需改业务代码。
            try:
                path = self.browser.dump_html(page, "grades_empty")
                log.warning("未解析到成绩明细，已导出页面结构供适配选择器：%s", path)
            except Exception:
                log.debug("成绩页面导出失败", exc_info=True)
        try:
            self.store.upsert_grades(int(course["id"]), rows, overview=overview)
        except Exception:
            log.debug("成绩落库失败", exc_info=True)
        return {"overview": overview, "rows": rows}

    def _fetch_overall_detail(self, page: Any, opened: bool) -> list[dict[str, Any]]:
        """在“综合成绩”所在页/新标签里,读考核标准权重与(若已渲染)各模块得分行。

        点“综合成绩”常弹新标签,需给其加载时间;故对候选页做“等待加载 + 有界重试”。
        """
        from course.grades import merge_grade_rows, parse_criteria_modules, parse_grade_rows

        data = None
        attempts = 5 if opened else 1
        for i in range(attempts):
            pages = [page]
            if opened:
                try:
                    ctx = getattr(page, "context", None)
                    pages += [pg for pg in (ctx.pages if ctx else []) if pg is not page]
                except Exception:
                    log.debug("枚举综合成绩新标签失败", exc_info=True)
            for pg in pages:
                try:
                    pg.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                for frame in [pg, *A.frames_of(pg)]:
                    try:
                        got = A.js_eval(frame, OVERALL_JS)
                    except Exception:
                        got = None
                    if got and (got.get("modules") or got.get("table")):
                        data = got
                        break
                if data:
                    break
            if data or not opened:
                break
            _time.sleep(1.2)
        if not data:
            return []
        rows = parse_criteria_modules(data.get("modules") or [])
        if data.get("table"):
            rows = merge_grade_rows(rows, parse_grade_rows(data.get("table")))
        return rows

    def _knowledge_count(self, rows: list[dict[str, Any]]) -> int:
        """真正的“小节”链接数量：学习通章节目录里的小节都带 /knowledge/ 或 jobid。"""
        n = 0
        for r in rows:
            u = str(r.get("url", "")).lower()
            if "/knowledge/" in u or "jobid=" in u or "/work/job" in u:
                n += 1
        return n

    def _looks_like_catalog(self, rows: list[dict[str, Any]]) -> bool:
        """有“小节链接”才算目录页；只有资料/作业/考试等门户分类入口的不算。"""
        return self._knowledge_count(rows) >= 1

    def _catalog_tab_urls(self, page: Any) -> list[str]:
        js = r"""
        () => {
          const out = [];
          document.querySelectorAll("a[href]").forEach((a) => {
            const t = (a.innerText || '').replace(/\s+/g, '').trim();
            if (/^(章节|目录|章节任务|学习)$/.test(t) || /knowledge|studycourse/.test(a.href)) out.push(a.href);
          });
          return Array.from(new Set(out)).slice(0, 10);
        }
        """
        current = ""
        try:
            current = str(page.url)
        except Exception:
            pass
        # 排除小节/文档/考试等“叶子链接”，只保留可能是目录/课程页的地址
        bad = ("jobid=", "/knowledge/section", "/doc/", "/ppt/", "/exam/", "/work/toWork", "ananas")
        found = A.js_eval(page, js) or []
        out = []
        for u in found:
            s = str(u or "")
            if not s.startswith("http"):        # javascript:、#、空值等一律跳过
                continue
            if s == current or any(b in s for b in bad):
                continue
            out.append(s)
        return out[:8]

    def _is_chapter_head(self, text: str, url: str, flagged: bool = False) -> bool:
        """判断某一行是不是“章标题”。flagged=页面结构已标注为分组标题。"""
        if not text:
            return False
        named = bool(CHAPTER_HEAD_RE.match(text)) and len(text) <= 45
        if flagged and named:
            return True
        if named and (not url or url.endswith(".com") or "javascript:" in url or url.endswith("#")):
            return True
        return False

    def _make_chapter(self, text: str, prev: Chapter | None, idx: int) -> Chapter:
        m = CHAPTER_HEAD_RE.match(text)
        no = one_line(m.group(1)) if m else ""
        title = (m.group(3) if m and m.group(3) else text) or f"章节{idx + 1}"
        level = 2 if re.match(r"^\d{1,2}\.\d{1,2}", no or text) else 1
        parent = ""
        if level == 2 and prev and prev.level == 1:
            parent = prev.chap_key
        return Chapter(chap_key=f"ch_{idx + 1}_{_sha(no + title, 6)}", title=clean_text(title)[:60],
                       no=no, level=level, parent_key=parent, order_idx=idx)

    def _lookup_status(self, text: str, index: dict[str, Any]) -> bool | None:
        """
        把“完成状态标记”对应到某个小节标题上。

        平台标记常与标题在同一个 li/tr 里（如 “1.1 电路基本概念 未学”），
        因此这里做“互相包含 + 最长重叠”匹配，而不是要求完全相等。
        """
        key = one_line(text)[:40]
        if not key or not index:
            return None
        if key in index and index[key] is not None:
            return bool(int(index[key]))
        best_key, best_len = None, 0
        for candidate, value in index.items():
            if not candidate or value is None:
                continue
            if candidate.startswith(key) or key in candidate or candidate in key:
                overlap = len(set(candidate) & set(key))
                if overlap > best_len:
                    best_key, best_len = candidate, overlap
        if best_key is not None:
            return bool(int(index[best_key]))
        return None

    # ------------------------------------------------------------ 类型识别
    def classify_kind(self, url: str, text: str = "") -> str | None:
        """返回 video|document|ppt|exam|work|practice|discussion|live|other；无意义链接返回 None。"""
        u = (url or "").lower()
        t = (text or "").lower()
        if "chaoxing.com" not in u and u.startswith("http"):
            return None
        if any(p in u for p in ("logout", "/studentcourse/selectcourse", "javascript:", "mailto:")):
            return None
        if any(p in u for p in URL_PATTERNS["video"]):
            return "video"
        if any(p in u for p in URL_PATTERNS["ppt"]):
            return "ppt"
        if any(p in u for p in URL_PATTERNS["document"]) or any(
            u.endswith(x) for x in (".pdf", ".docx", ".doc", ".pptx", ".ppt", ".txt")
        ):
            return "document"
        if any(p in u for p in URL_PATTERNS["exam"]):
            return "exam" if "考试" in t or "测验" in t else "exam"
        if any(p in u for p in URL_PATTERNS["work"]):
            return "practice" if any(k in t for k in ("练习", "自测", "题库")) else "work"
        if any(p in u for p in URL_PATTERNS["section"]):
            # 章节学习页：用文本关键词二次判定
            for kind, words in KIND_KEYWORDS.items():
                if any(w in t for w in words):
                    return kind if kind != "practice" else "practice"
            return "video" if "video" in u else "other"
        for kind, words in KIND_KEYWORDS.items():
            if any(w in t for w in words):
                return kind
        return None

    def discover_courses_by_clicking(self, page, limit: int = 12) -> list[Course]:
        """
        第三级发现策略：个人空间里的“我学的课”是 Vue 渲染的 div（没有可用链接），
        只能**逐个点开**，再从跳转后的地址里取 courseId/cpi/clazzid。
        等价于你自己挨个点一遍课程，不构造任何请求。
        """
        import time as _t

        found: dict[str, Course] = {}

        def scan_cards():
            """课程卡片在个人空间的子 iframe 里，必须遍历所有 frame 才找得到。"""
            out: list[tuple[Any, str]] = []
            seen: set[str] = set()
            for fr in A.frames_of(page):
                for c in (A.js_eval(fr, SPACE_CARDS_JS, limit) or []):
                    label = str(c.get("label", ""))
                    if label and label not in seen:
                        seen.add(label)
                        out.append((fr, label))
            return out

        # 卡片只在“个人空间”页面出现：先过去，再等异步 iframe 渲染出来
        home = self.cfg.get("CHAOXING_HOME_URL") or "https://i.chaoxing.com"
        A.safe_goto(page, f"{home.rstrip('/')}/base", attempts=2)
        cards = scan_cards()
        for _ in range(8):
            if cards:
                break
            _t.sleep(2.0)
            cards = scan_cards()
        if not cards:
            log.info("个人空间里没有课程卡片（可能账号未选课，或该页结构已改版）")
            return []
        log.info("发现 %s 个候选课程卡片，逐个点开确认…", len(cards))
        budget = _t.time() + min(180.0, 18.0 * max(1, len(cards)))
        for fr, label in cards:
            if _t.time() > budget or len(found) >= limit:
                log.info("课程发现达到时间/数量上限，已找到 %s 门", len(found))
                break
            try:
                loc = fr.locator(f'[aria-label="{label}"], [title="{label}"]').first
                if loc.count() == 0:
                    continue
                loc.scroll_into_view_if_needed(timeout=2500)
                loc.click(timeout=3500)
            except Exception as exc:
                log.debug("点开课程卡片失败 %s：%s", label[:20], exc)
                continue
            _t.sleep(3)
            for pg in self.browser.tabs():
                try:
                    urls = pg.evaluate(COURSE_URL_JS) or []
                except Exception:
                    urls = []
                for u in urls:
                    if "courseid" not in u.lower():
                        continue
                    ids = _query(u, "courseId", "cpi", "clazzid")
                    cid = ids.get("courseId") or ids.get("id") or ""
                    if not cid:
                        continue
                    key = Course.make_key(cid, ids.get("cpi", ""), ids.get("clazzid", ""))
                    if key in found:
                        continue
                    title = label
                    try:
                        pt = " ".join(str(pg.title() or "").split())
                        if pt and "chaoxing" not in pt.lower() and len(pt) <= 40:
                            title = pt
                    except Exception:
                        pass
                    found[key] = Course(course_key=key, name=title[:60], url=u,
                                        cpi=ids.get("cpi", ""), clazzid=ids.get("clazzid", ""))
                    break
            # 回到空间页，继续下一个
            try:
                home = self.cfg.get("CHAOXING_HOME_URL") or "https://i.chaoxing.com"
                A.safe_goto(page, f"{home.rstrip('/')}/base", attempts=1)
                _t.sleep(2.5)
                for fr2 in A.frames_of(page):
                    if label in {str(x.get("label", "")) for x in (A.js_eval(fr2, SPACE_CARDS_JS, 3) or [])}:
                        fr = fr2
                        break
            except Exception:
                pass
            if len(found) >= limit:
                break
        return list(found.values())


    # ------------------------------------------------------------ 小节内部任务
    def inspect_section(self, page: Any) -> dict[str, Any]:
        """
        进入一个小节后，识别页面里包含哪些任务（视频/文档/PPT/测验）。
        小节页内容通常在 iframe 内，因此需要跨 frame 扫描。
        """
        info: dict[str, Any] = {"kinds": [], "items": [], "url": page.url}
        for frame in A.frames_of(page):
            try:
                body_len = A.js_eval(frame, "() => (document.body && document.body.innerText || '').length") or 0
            except Exception:
                body_len = 0
            if not body_len:
                continue
            kinds = []
            for key, sel_key in (("video", "video_tag"), ("ppt", "ppt_module"), ("document", "doc_module")):
                if A.first_element(frame, SELECTORS[sel_key]):
                    kinds.append(key)
            links = A.js_eval(frame, COLLECT_LINKS_JS) or []
            for row in links:
                kind = self.classify_kind(row.get("url", ""), row.get("text", ""))
                if kind in ("exam", "work", "practice", "document", "video", "ppt"):
                    info["items"].append({"kind": kind, "title": one_line(row.get("text", ""))[:100],
                                          "url": row.get("url", "")})
                    kinds.append(kind)
            for k in kinds:
                if k not in info["kinds"]:
                    info["kinds"].append(k)
        return info

TASK_LIST_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  const sels = ['li', 'tr', '.bottomList li', '.workList li', '.Tbox4 .pk-tpo', 'div[class*=item]'];
  const seen = new Set();
  sels.forEach((sel) => {
    Array.prototype.slice.call(document.querySelectorAll(sel)).forEach((el, i) => {
      const text = norm(el.innerText);
      if (!text || text.length < 6 || text.length > 600) return;
      if (!/(作业|测验|考试|任务|练习|Chapter|第.{1,6}次)/.test(text)) return;
      const link = el.querySelector('a[href]');
      const href = link ? link.href : '';
      const oc = el.querySelector('[onclick]');
      const key = (href || '') + '|' + text.slice(0, 40);
      if (seen.has(key)) return;
      seen.add(key);
      let top = 0;
      try { top = Math.round(el.getBoundingClientRect().top + window.scrollY); } catch (e) {}
      out.push({
        text: text.slice(0, 300),
        href: href,
        hasOnclick: !!oc,
        status: (text.match(/(未交|待完成|已完成|已提交|待批阅|已批阅|未开始|进行中|已结束|补考|已过期)/g) || []).join(','),
        score: (text.match(/(得分|成绩|分数)[^0-9]{0,4}([0-9]+(\.[0-9]+)?)/) || [])[2] || '',
        top: top,
        index: out.length,
        tag: el.tagName,
        cls: (el.className || '').toString().slice(0, 60)
      });
    });
  });
  return {title: document.title, url: location.href, items: out.slice(0, 60)};
}
"""

