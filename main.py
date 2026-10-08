# -*- coding: utf-8 -*-
"""
main.py —— 学习通「课程学习 + 刷题 + 错题整理 + 开卷期末 AI 辅助」工具

运行入口（详见 README.md）：
    python main.py menu                 # ★推荐：浏览器常驻，登录一次连续操作
    python main.py login
    python main.py courses
    python main.py sync  --course 电路
    python main.py study --course 电路 --practice
    python main.py practice --course 电路 --url <练习页地址>
    python main.py kb  build --course 电路
    python main.py search 戴维南定理
    python main.py wrong list / export --fmt md / drill --limit 10
    python main.py exam --course 电路          # 需考试明确允许 AI，且通过三重确认
    python main.py status
    python main.py dump --url <页面地址>
    python main.py selftest            # 离线逻辑自检
    python main.py btest               # 离线页面结构自检（本地 fixture + Chromium）

合规原则：登录/验证码/身份验证由用户本人完成；程序对考试页面只读；
绝不代提交、绝不改倒计时、绝不绕过任何限制。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import CONFIG, SAFETY                       # noqa: E402
from utils.console import (ask, banner, confirm, err, info, init_console, ok, section,
                          warn)  # noqa: E402
from utils.logger import get_logger, setup_logging      # noqa: E402


@dataclass
class App:
    """依赖装配：一次构造，各模块共享（避免循环导入）。"""

    cfg: Any
    store: Any
    headless: bool = False
    keep_open: bool = False
    browser: Any = None
    crawler: Any = None
    watcher: Any = None
    ai: Any = None
    engine: Any = None
    kb: Any = None
    extractor: Any = None
    wrongbook: Any = None
    practice: Any = None
    popup: Any = None
    study: Any = None

    def need_browser(self) -> Any:
        if self.browser is None:
            from browser.driver import Browser

            self.browser = Browser(self.cfg, self.store, headless=self.headless)
        return self.browser

    def need_ai(self) -> Any:
        if self.ai is None:
            from ai.client import AIClient

            self.ai = AIClient(self.cfg, store=self.store)
            from ai.responder import AnswerEngine

            self.engine = AnswerEngine(ai=self.ai, cfg=self.cfg, store=self.store)
        return self.ai

    def need_kb(self) -> Any:
        if self.kb is None:
            from knowledge_base.builder import KnowledgeBase
            from knowledge_base.retriever import Retriever

            builder = KnowledgeBase(store=self.store, cfg=self.cfg, browser=None, engine=None)
            self.kb = _KBFacade(builder=builder, retriever=Retriever(self.store, self.cfg))
        return self.kb

    def need_practice(self) -> Any:
        if self.practice is None:
            from questions.extractor import QuestionExtractor
            from questions.practice import PracticeRunner
            from questions.wrongbook import WrongBook

            self.need_ai()
            self.extractor = QuestionExtractor(self.cfg)
            self.wrongbook = WrongBook(store=self.store, engine=self.engine, cfg=self.cfg)
            self.practice = PracticeRunner(
                browser=self.need_browser(), cfg=self.cfg, store=self.store,
                engine=self.engine, extractor=self.extractor, kb=self.need_kb(), wrongbook=self.wrongbook,
            )
        return self.practice

    def need_crawler_offline(self) -> Any:
        """只需要解析能力的 crawler（不启动浏览器）。"""
        from course.crawler import CourseCrawler

        self.crawler = self.crawler or CourseCrawler(browser=None, cfg=self.cfg, store=self.store)
        return self.crawler

    def need_study(self) -> Any:
        if self.study is None:
            from course.crawler import CourseCrawler
            from course.study import StudyRunner
            from video.player import VideoWatcher

            browser = self.need_browser()
            self.crawler = self.crawler or CourseCrawler(browser=browser, cfg=self.cfg, store=self.store)
            from questions.popup import PopupWatcher

            self.watcher = VideoWatcher(browser=browser, cfg=self.cfg, store=self.store)
            self.popup = PopupWatcher(cfg=self.cfg, store=self.store, engine=self.engine, kb=self.need_kb(),
                                      min_interval_sec=float(self.cfg.get("POPUP_MIN_INTERVAL_SEC") or 5.0))
            self.watcher.popup = self.popup
            self.study = StudyRunner(
                browser=browser, cfg=self.cfg, store=self.store, crawler=self.crawler,
                watcher=self.watcher, kb=self.need_kb(), practice=self.need_practice(),
                popup=self.popup,
            )
        return self.study

    def pick_course(self, needle: str = "") -> dict[str, Any] | None:
        """课程选择入口：一律走“勾选确认”。匹配到多门时必须由你选，程序绝不替你挑。"""
        return choose_course_interactive(self, needle)


class _KBFacade:
    """把 builder 与 retriever 合成一个“知识库”对象，供各模块注入使用。"""

    def __init__(self, builder: Any, retriever: Any) -> None:
        self.builder = builder
        self.retriever = retriever

    # 检索
    def search(self, *a: Any, **k: Any) -> Any:
        return self.retriever.search(*a, **k)

    def render_hits(self, *a: Any, **k: Any) -> Any:
        return self.retriever.render_hits(*a, **k)

    def evidence_for_question(self, *a: Any, **k: Any) -> Any:
        return self.retriever.evidence_for_question(*a, **k)

    def parse_query(self, *a: Any, **k: Any) -> Any:
        return self.retriever.parse_query(*a, **k)

    # 入库
    def ingest_file(self, *a: Any, **k: Any) -> Any:
        return self.builder.ingest_file(*a, **k)

    def ingest_page(self, *a: Any, **k: Any) -> Any:
        return self.builder.ingest_page(*a, **k)

    def ingest_local_dir(self, *a: Any, **k: Any) -> Any:
        return self.builder.ingest_local_dir(*a, **k)

    def build_from_items(self, *a: Any, **k: Any) -> Any:
        return self.builder.build_from_items(*a, **k)

    def download(self, *a: Any, **k: Any) -> Any:
        return self.builder.download(*a, **k)

    def rebuild_index(self, *a: Any, **k: Any) -> Any:
        return self.retriever._ensure_index(force=True)

    def bind_browser(self, browser: Any) -> None:
        """知识库下载课件需要用到已登录的浏览器会话。"""
        self.builder.browser = browser

    def bind_engine(self, engine: Any) -> None:
        """有 AI 时用 AI 提炼知识点，没有则退回规则法。"""
        self.builder.engine = engine


# ------------------------------------------------------------------ 命令实现
def cmd_login(app: App) -> int:
    banner("登录学习通（账号/验证码/身份验证由你本人完成）")
    browser = app.need_browser()
    if browser.wait_for_manual_login():
        ok("登录状态已确认；浏览器用户目录已保存，下次运行免重复登录。")
        browser.save_state_note()
        return 0
    err("未检测到登录状态。程序不会尝试自动登录，也不会绕过验证码/身份验证。")
    return 2


def cmd_courses(app: App) -> int:
    banner("课程列表同步")
    browser = app.need_browser()
    if not browser.wait_for_manual_login():
        return 2
    from course.crawler import CourseCrawler

    crawler = CourseCrawler(browser=browser, cfg=app.cfg, store=app.store)
    courses = crawler.sync_courses()
    if not courses:
        err("没有获取到课程，请检查登录状态或页面结构（可用 dump 命令导出排查）")
        return 3
    section(f"共 {len(courses)} 门课程")
    for c in courses:
        info(f"{c.name}  course_key={c.course_key}")
    return 0


# ------------------------------------------------------------------ 现场抓取（调试用）
def _dump_frames(app: App, page, out_dir: Path, tag: str) -> dict:
    """把当前页面所有 frame 的 HTML 与关键统计写入文件，返回 manifest 片段。"""
    import json as _json

    from browser import actions as A

    frames = []
    for i, fr in enumerate(page.frames):
        try:
            html = fr.content()
        except Exception as exc:
            frames.append({"index": i, "url": str(fr.url)[:300], "error": str(exc)[:200]})
            continue
        fn = out_dir / f"{tag}_frame{i:02d}.html"
        fn.write_text(html, encoding="utf-8")
        try:
            stat = fr.evaluate("""() => ({
                anchors: document.querySelectorAll('a').length,
                toOld: document.querySelectorAll('a[onclick]').length,
                toOldReal: [...document.querySelectorAll('a[onclick]')]
                              .filter(a => (a.getAttribute('onclick')||'').indexOf('toOld') >= 0).length,
                video: document.querySelectorAll('video').length,
                iframe: document.querySelectorAll('iframe').length,
                bodyLen: (document.body && document.body.innerText || '').length,
                title: document.title || '',
                onclickNames: [...new Set([...document.querySelectorAll('a[onclick]')]
                     .map(a => ((a.getAttribute('onclick')||'').split('(')[0]||'').trim()))].slice(0, 15),
                sample: [...document.querySelectorAll('a[onclick]')].slice(0, 8)
                     .map(a => ({t: (a.innerText||a.title||'').replace(/\\s+/g,' ').trim().slice(0,24),
                                 oc: (a.getAttribute('onclick')||'').replace(/\\s+/g,' ').slice(0,90)}))
            })"""  )
        except Exception as exc:
            stat = {"error": str(exc)[:200]}
        frames.append({"index": i, "url": str(fr.url)[:300], "file": fn.name,
                       "bytes": len(html), **stat})
    (out_dir / f"{tag}_frames.json").write_text(
        _json.dumps(frames, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"tag": tag, "url": str(page.url)[:300], "frames": frames}


def cmd_capture(app: App, needle: str = "", wait: int = 40, open_section: bool = True) -> int:
    """
    抓取真实页面现场：门户页 → 点“章节”标签 → （可选）点第一个小节。
    所有 HTML 与统计写入 data/capture/<时间戳>/，之后可离线分析，不必再登录。
    """
    import json as _json
    import time as _t

    from course.crawler import CourseCrawler

    banner(f"抓取现场（capture）：{needle or '默认课程'}",
           "只读浏览你自己的课程页面，把结构存成本地文件；不会修改平台任何数据。")
    out_dir = BASE_DIR / "data" / "capture" / _t.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    browser = app.need_browser()
    if not browser.wait_for_manual_login():
        err("未检测到登录状态：请在弹出窗口完成登录（本次之后会保存 Cookie）。")
        return 2
    course = app.pick_course(needle)
    if not course:
        return 2
    page = browser.start().page
    crawler = CourseCrawler(browser=browser, cfg=app.cfg, store=app.store)
    manifest: dict = {"course": course, "steps": []}

    info("① 打开课程门户页…")
    from browser.actions import safe_goto
    safe_goto(page, course["url"], attempts=2)
    _t.sleep(4)
    manifest["steps"].append(_dump_frames(app, page, out_dir, "01_portal"))

    info("② 点击“章节”标签并轮询等待小节渲染…")
    clicked = crawler._open_chapter_tab(page)
    ok(f"已点击章节标签：{clicked}")
    sections_found = 0
    deadline = _t.time() + max(10, int(wait))
    while _t.time() < deadline:
        cat = crawler.parse_fanya_catalog(page)
        sections_found = len(cat.get("sections") or [])
        if sections_found:
            break
        _t.sleep(2.5)
    _t.sleep(2)
    manifest["steps"].append(_dump_frames(app, page, out_dir, "02_chapter"))
    cat = crawler.parse_fanya_catalog(page)
    manifest["chapter_parse"] = {"sections": sections_found,
                                 "chapters": len(cat.get("chapters") or []),
                                 "summary": (cat.get("summary") or "")[:200],
                                 "sample": (cat.get("sections") or [])[:5]}
    info(f"③ 解析结果：小节 {sections_found} 个 / 章 {len(cat.get('chapters') or [])} 个")
    try:
        manifest["catalog_url"] = str(page.url)[:300]
        app.store.set_meta(f"catalog_url:{course['id']}", manifest["catalog_url"])
    except Exception:
        pass

    if open_section and cat.get("sections"):
        s0 = (cat["sections"] or [{}])[0]
        info(f"④ 打开第一个小节：{str(s0.get('title'))[:30]}（jobId={s0.get('jobId')}）")
        if crawler.open_item(page, {"url": f"fanya://toOld/{s0.get('courseId')}/{s0.get('jobId')}/{s0.get('clazzid')}",
                                    "course_id": course["id"], "title": s0.get("title", "")},
                             manifest.get("catalog_url", "")):
            _t.sleep(4)
            step = _dump_frames(app, page, out_dir, "03_section")
            manifest["steps"].append(step)
            kinds = crawler.inspect_section(page)
            manifest["section_kinds"] = kinds
            info(f"   小节页类型识别：{kinds.get('kinds')}")
    else:
        warn("没有可点的小节（解析为空）；现场文件已保存，可离线分析。")

    (out_dir / "manifest.json").write_text(_json.dumps(manifest, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    browser.stop()      # 正常关闭：Cookie 会落盘，避免下次再登录
    section(f"抓取完成")
    info(f"现场目录：{out_dir}")
    info(f"小节解析：{sections_found} 个 ｜ 章：{len(cat.get('chapters') or [])} 个")
    info("把该目录发我（或直接说“分析现场”），我离线改解析器，不需要你再登录。")
    return 0 if sections_found else 3


def hold_browser(app: App, seconds: int = 600) -> None:
    """命令结束后保持浏览器打开：避免“窗口突然消失”，也保住登录态。"""
    if not getattr(app, "keep_open", False):
        return
    import time as _t

    print("\n" + "=" * 62)
    info("按要求保持浏览器打开中（窗口不会自动关闭）。")
    if _is_tty():
        try:
            input("    看完后按回车关闭浏览器并结束命令…")
        except (EOFError, KeyboardInterrupt):
            print()
        return
    info(f"    非交互环境：保持 {seconds} 秒后自动结束（期间窗口一直可用）。")
    for left in range(seconds, 0, 20):
        _t.sleep(min(20, left))
    print()


def _sync_from_html(app: App, course: dict, html_path: Path) -> int:
    """离线解析已抓取的章节页 HTML 并写入本地库（不访问学习通、不需要登录）。"""
    import json as _json

    from course.crawler import FANYA_CATALOG_JS
    from course.models import Course
    from playwright.sync_api import sync_playwright

    if not html_path.exists():
        err(f"文件不存在：{html_path}")
        return 2
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="load")
        cat = page.evaluate(FANYA_CATALOG_JS)
        browser.close()
    crawler = app.need_crawler_offline()
    c = Course(course_key=course["course_key"], name=course["name"], url=course.get("url", ""),
               cpi=course.get("cpi", ""), clazzid=course.get("clazzid", ""), id=int(course["id"]))
    chapters, items = crawler._chapters_from_fanya(cat, c)
    if not items:
        err("该 HTML 里没有解析到小节（可能抓的是门户页而非章节页）。")
        return 3
    app.store.replace_chapters(int(course["id"]), [ch.to_dict() for ch in chapters])
    for it in items:
        row = app.store.upsert_item(int(course["id"]), it.to_dict())
        if it.done and not row["done"]:
            app.store.finish_item(row["id"], "章节页标注已完成")
    man = html_path.parent / "manifest.json"
    if man.exists():
        try:
            catalog_url = _json.loads(man.read_text(encoding="utf-8")).get("catalog_url", "")
            if catalog_url:
                app.store.set_meta(f"catalog_url:{course['id']}", catalog_url)
        except Exception:
            pass
    ok(f"离线导入完成：{len(chapters)} 章 / {len(items)} 小节（{cat.get('summary','')[:30]}）")
    info("下一步：python main.py go --course %s --limit 2 --keep" % course["name"])
    return 0


def cmd_sync(app: App, needle: str, yes: bool = False, args=None, hold: bool = True) -> int:
    banner("章节目录同步")
    course = app.pick_course(needle)                      # 先勾选课程
    if not course:
        return 2
    cap = str(getattr(args, "from_capture", "") or "") if args is not None else ""
    if cap:                                               # 离线解析已抓取页面，不需要登录
        return _sync_from_html(app, course, Path(cap))
    if not confirm_action("同步章节目录",                   # 再确认，确认后才开浏览器
                          [f"课程：{course['name']}", f"courseId/cpi：{course['course_key']}",
                           "动作：只读取页面内容写入本地库（不修改平台数据）"],
                          skip=yes):
        return 4
    browser = app.need_browser()
    if not browser.wait_for_manual_login():
        return 2
    from course.crawler import CourseCrawler
    from course.models import Course

    crawler = CourseCrawler(browser=browser, cfg=app.cfg, store=app.store)
    c = Course(course_key=course["course_key"], name=course["name"], url=course.get("url", ""),
               cpi=course.get("cpi", ""), clazzid=course.get("clazzid", ""), id=int(course["id"]))
    catalog = crawler.sync_catalog(c)
    # 回填真实课程名：目录页标题通常就是《课程名》
    try:
        real = " ".join(str(crawler.browser.start().page.title() or "").split())
    except Exception:
        real = ""
    for junk in ("章节目录", "课程", "-", "学习通"):
        real = real.replace(junk, "")
    real = real.strip()
    if real and len(real) >= 3 and real != course["name"]:
        app.store.exec("UPDATE courses SET name=? WHERE id=?", (real[:60], int(course["id"])))
        ok(f"课程名已按页面标题回填：{course['name']} → {real}")
        course["name"] = real
    section(f"{course['name']}：{len(catalog.chapters)} 章 / {len(catalog.items)} 个学习项")
    counts: dict[str, int] = {}
    for it in catalog.items:
        counts[it.kind] = counts.get(it.kind, 0) + 1
    for kind, n in sorted(counts.items(), key=lambda x: -x[1]):
        info(f"{kind}: {n}")
    if hold:
        hold_browser(app)
    return 0


def cmd_study(app: App, args: argparse.Namespace) -> int:
    banner("自动学习（视频正常速度播放 + 文档阅读 + 进度记录）")
    print(_safety_note())
    course = app.pick_course(args.course)                  # 先勾选课程
    if not course:
        return 2
    kinds_txt = str(getattr(args, "kinds", "") or "")
    kinds = [k.strip() for k in kinds_txt.split(",") if k.strip()] if kinds_txt else None
    todo = app.store.list_items(int(course["id"]), only_unfinished=not args.all,
                                kinds=kinds or None, limit=1000)
    if not todo:
        warn("本地没有待处理的学习项，请先运行：python main.py sync --course \"课程名\"")
        return 3
    cnt: dict[str, int] = {}
    for it in todo:
        cnt[it["kind"]] = cnt.get(it["kind"], 0) + 1
    _pg = app.need_browser().start().page
    if not require_pledge_signed(app, _pg, f"python main.py study --course {args.course}"):
        return 5
    if not confirm_action("开始自动学习",
                          [f"课程：{course['name']}",
                           f"待处理：{len(todo)} 项（{', '.join(f'{k}×{v}' for k, v in cnt.items())}）",
                           f"本次上限：{getattr(args, 'limit', 0) or app.cfg.get('MAX_ITEMS_PER_RUN')} 项",
                           f"视频倍速：{app.cfg.get('VIDEO_PLAYBACK_RATE')}x（不可加速）",
                           f"遇练习进入刷题：{'是' if args.practice else '否'}",
                           "浏览器需保持前台；作答与提交一律由你本人完成"], skip=getattr(args, "yes", False)):
        return 4
    runner = app.need_study()
    runner.run(course, limit=getattr(args, "limit", 0), kinds=kinds, auto_practice=getattr(args, "practice", False),
               build_kb=not getattr(args, "no_kb", False), redo_all=getattr(args, "all", False))
    hold_browser(app)
    return 0


def cmd_practice(app: App, args: argparse.Namespace) -> int:
    _auto = getattr(args, "auto_answer", False) or getattr(args, "auto_submit", False)
    banner("刷题辅助（练习/演示模式：可自动作答/提交）" if _auto
           else "刷题辅助（AI 分析 + 本地资料依据，提交由你完成）")
    course = app.pick_course(args.course)
    if not course:
        return 2
    if not confirm_action("进入刷题辅助",
                          [f"课程：{course['name']}",
                           "AI 只给答案与解析；作答与提交全部由你本人在学习通页面完成"],
                          skip=getattr(args, "yes", False)):
        return 4
    app.need_browser().wait_for_manual_login()
    runner = app.need_practice()
    url = args.url or ""
    stat = runner.run(course, url=url, chapter_key=args.chapter or "", limit=args.limit,
                      auto_answer=getattr(args, "auto_answer", False),
                      auto_submit=getattr(args, "auto_submit", False))
    return 0 if stat else 3


def cmd_kb(app: App, args: argparse.Namespace) -> int:
    kb = app.need_kb()
    if args.action == "local":
        banner("把本地资料目录纳入知识库")
        target = Path(args.dir or str(app.cfg.path("KB_DOWNLOAD_DIR")))
        stat = kb.ingest_local_dir(target, course_id=(app.pick_course(args.course) or {}).get("id"))
        ok(f"扫描 {stat['found']} 个文件，入库 {stat['done']}，跳过 {stat['skip']}，失败 {stat['fail']}，"
           f"共 {stat['chunks']} 个知识块")
        return 0
    if args.action == "search":
        banner("知识库检索")
        result = kb.search(" ".join(args.query or [""]), course_id=(app.pick_course(args.course) or {}).get("id"))
        print(kb.render_hits(result, limit=args.top))
        return 0
    if args.action == "docs":
        section("已入库资料")
        for d in app.store.kb_docs():
            info(f"[{d['id']}] {d['status']:<8} {d['doc_type']:<6} {d['chars']:>7} 字  {d['title'][:50]}")
        return 0
    from browser.actions import safe_goto

    banner("课程资料入库（逐个打开小节抓取正文）")
    browser = app.need_browser()
    if not browser.wait_for_manual_login():
        return 2
    course = app.pick_course(args.course)
    if not course:
        return 2
    kb.bind_browser(browser)
    if app.cfg.ai_enabled:                      # 有 AI 时用 AI 提炼知识点，否则退回规则法
        app.need_ai()
        kb.bind_engine(app.engine)
    items = app.store.list_items(int(course["id"]), only_unfinished=False, limit=500)
    if not items:
        warn("本地没有学习项，先执行 sync")
        return 3

    # fanya:// 小节没有文件可下载：改为逐个打开小节页面抓正文
    page = browser.start().page
    catalog_url = str(app.store.get_meta(f"catalog_url:{course['id']}", "") or "")
    from course.crawler import CourseCrawler
    crawler = app.crawler or CourseCrawler(browser=browser, cfg=app.cfg, store=app.store)
    cap = int(args.limit or 20)
    done = chunks = skipped = failed = 0
    section(f"开始抓取小节正文（最多 {cap} 项）")
    for item in items[:cap]:
        url = str(item.get("url") or "")
        ok_open = (url.startswith("http") and safe_goto(page, url, attempts=2)) or                   crawler.open_item(page, {**item, "course_id": course["id"]}, catalog_url)
        if not ok_open:
            failed += 1
            info(f"× 打不开：{item['title'][:34]}")
            continue
        try:
            res = kb.ingest_page(page, course_id=int(course["id"]),
                                 chapter_key=item.get("chapter_key", ""), title=item.get("title", ""))
        except Exception as exc:
            failed += 1
            log.warning("正文入库失败：%s", exc)
            continue
        if res.get("status") == "done":
            done += 1
            chunks += int(res.get("chunks") or 0)
            ok(f"√ {item['title'][:34]} → {res.get('chunks')} 块")
        else:
            skipped += 1
            info(f"- 跳过：{item['title'][:30]}（{res.get('error', '')}）")
    ok(f"入库完成：{done} 个小节 / {chunks} 个知识块；跳过 {skipped}，失败 {failed}")
    info("现在可以试：python main.py search 改革开放 历史 基点 --ai")
    return 0


def cmd_search(app: App, args: argparse.Namespace) -> int:
    query = " ".join(args.query).strip()
    banner(f"统一搜索：{query}")
    kb = app.need_kb()
    course = app.pick_course(args.course) if args.course else None
    result = kb.search(query, course_id=(course or {}).get("id"), top_k=args.top)
    print(kb.render_hits(result, limit=args.top))
    if args.ai and result.get("chunks"):
        app.need_ai()
        from ai.responder import AnswerEngine

        engine = app.engine or AnswerEngine(ai=app.ai, cfg=app.cfg, store=app.store)
        summary = engine.summarize(query, list(result["chunks"]))
        if summary:
            section("AI 综合")
            print(summary)
    if not result.get("has_evidence"):
        warn("本地资料里没有匹配内容。建议先执行：python main.py kb build --course <课程名>")
    return 0


def cmd_wrong(app: App, args: argparse.Namespace) -> int:
    from questions.wrongbook import WrongBook

    if app.cfg.ai_enabled:
        app.need_ai()
    wb = app.wrongbook or WrongBook(store=app.store, engine=app.engine, cfg=app.cfg)
    app.wrongbook = wb
    course = app.pick_course(args.course) if args.course else None
    cid = (course or {}).get("id")
    if args.action == "list":
        section("错题库")
        rows = wb.list(course_id=cid, only_unresolved=not args.all)
        if not rows:
            info("错题库为空（做完练习/考试判分后会自动整理进来）")
        for i, r in enumerate(rows[: args.limit], 1):
            info(f"{i}. [{r['kind']}] 错{r['wrong_count']}次 ｜ {r['stem'][:60]}")
            if r.get("knowledge"):
                info(f"     知识点：{r['knowledge']}｜答案：{r.get('answer') or '待补充'}")
        return 0
    if args.action == "stats":
        print(wb.stats_text())
        return 0
    if args.action == "export":
        wb.export(fmt=args.fmt, course_id=cid)
        return 0
    if args.action == "similar":
        row = app.store.get_question(args.id) or {}
        if not row:
            err("题目不存在，请用 `wrong list --all` 查看题目 id")
            return 2
        wb.gen_similar({**row, "course_id": cid}, n=args.n)
        return 0
    if args.action == "drill":
        runner = app.need_practice()
        return 0 if runner.wrongbook.practice_loop(course_id=cid, limit=args.limit, kb=app.need_kb()) is not None else 3
    return 2


def cmd_exam(app: App, args: argparse.Namespace) -> int:
    banner("开卷期末考试 AI 辅助（只读模式）")
    print(_safety_note())
    if not app.cfg.get("EXAM_MODE_ALLOWED"):
        err("user_config.py 中 EXAM_MODE_ALLOWED 仍为 False。\n"
            "只有当本课程期末考试**明确允许开卷并允许 AI 辅助**时，才可以把它改为 True。")
        return 2
    course = app.pick_course(args.course) or {}
    browser = app.need_browser()
    if not browser.wait_for_manual_login():
        return 2
    from exam.assistant import ExamAssistant, run_console_ui
    from exam.guard import ExamGuard
    from exam.worker import BrowserWorker

    app.need_ai()
    app.need_kb()
    guard = ExamGuard(app.cfg, app.store)
    worker = BrowserWorker(app.cfg, context={"store": app.store, "cfg": app.cfg})
    worker.start()
    if not worker.ready.wait(timeout=60):
        err("浏览器工作线程启动失败：" + (worker.error or "未知原因"))
        return 3
    assistant = ExamAssistant(cfg=app.cfg, store=app.store, worker=worker, guard=guard,
                              kb=app.need_kb(), engine=app.engine)
    try:
        if not assistant.bootstrap(course_name=course.get("name", ""), url=args.url or ""):
            return 2
        if args.gui:
            from ui.sidebar import run_gui_or_console

            run_gui_or_console(assistant, prefer=app.cfg.get("EXAM_UI") or "tkinter")
        else:
            run_console_ui(assistant)
    finally:
        assistant.close()
        worker.stop()
    ok("考试辅助已退出。请确认：所有答案的提交都由你本人在学习通页面完成。")
    return 0


def cmd_grades(app: App, args: argparse.Namespace) -> int:
    """只读查看课程成绩:默认实时抓取并缓存;--cache 仅看本地。"""
    from course.grades import render_grades

    course = app.pick_course(getattr(args, "course", "") or "")
    if not course:
        err("未找到课程,请先 `python main.py sync` 同步课程。")
        return 2
    if getattr(args, "cache", False):
        rows = app.store.list_grades(int(course["id"]))
        print(render_grades(rows, overview=app.store.get_grade_overview(int(course["id"]))))
        return 0
    browser = app.need_browser()
    if not browser.wait_for_manual_login():
        return 2
    from course.crawler import CourseCrawler

    crawler = app.crawler or CourseCrawler(browser=browser, cfg=app.cfg, store=app.store)
    app.crawler = crawler
    page = browser.start().page
    if page is None:
        err("浏览器页未就绪。")
        return 3
    try:
        data = crawler.fetch_grades(page, course)
    except Exception as exc:
        err(f"成绩抓取失败：{exc}")
        return 1
    print(render_grades(data.get("rows") or [], overview=data.get("overview") or ""))
    ok(f"成绩已只读读取并缓存(共 {len(data.get('rows') or [])} 条)。")
    return 0


def cmd_read(app: App, args: argparse.Namespace) -> int:
    """阅读/文档任务点：真实停留到估定时长，平台标注才算完成(不伪造)。"""
    course = app.pick_course(getattr(args, "course", "") or "")
    if not course:
        err("未找到课程，请先 sync。")
        return 2
    cid = int(course["id"])
    item_id = int(getattr(args, "item", 0) or 0)
    items = app.store.list_items(cid, only_unfinished=False, limit=5000)
    if item_id:
        it = next((i for i in items if int(i["id"]) == item_id), None)
    else:
        cands = [i for i in items if str(i.get("kind")) in ("document", "ppt", "other") and not i.get("done")]
        if not cands:
            ok("没有未完成的阅读/文档任务点。")
            return 0
        it = cands[0]
    if not it:
        err("找不到该小节。")
        return 2
    browser = app.need_browser()
    if not browser.wait_for_manual_login():
        return 2
    from course.crawler import CourseCrawler
    from video.player import VideoWatcher
    crawler = app.crawler or CourseCrawler(browser=browser, cfg=app.cfg, store=app.store)
    vw = VideoWatcher(browser=browser, cfg=app.cfg, store=app.store)
    page = browser.start().page
    catalog_url = str(app.store.get_meta(f"catalog_url:{course['id']}", "") or "")
    if not crawler.open_item(page, {**it, "course_id": course["id"]}, catalog_url):
        err("打开该小节失败。")
        return 1
    info(f"阅读/文档：{it.get('title','')[:40]}")
    mn = getattr(args, "min_seconds", None)
    res = vw.read_document(page, item=it, min_seconds=(mn or 45))
    if res.complete:
        app.store.finish_item(int(it["id"]), f"阅读停留 {res.watched_sec:.0f} 秒")
        ok(f"阅读任务完成（停留 {res.watched_sec:.0f} 秒）。")
    return 0


def cmd_discuss(app: App, args: argparse.Namespace) -> int:
    """计分讨论：--dump 只读导出讨论区结构；否则按三道门预览/执行发帖回复。"""
    from course.discussion import (DiscussionRunner, discuss_write_allowed,
                                   graded_discussion_targets)

    course = app.pick_course(getattr(args, "course", "") or "")
    if not course:
        err("未找到课程,请先 `python main.py sync` 同步课程。")
        return 2
    browser = app.need_browser()
    if not browser.wait_for_manual_login():
        return 2
    crawler = app.need_study_crawler() if hasattr(app, "need_study_crawler") else None
    if crawler is None:
        from course.crawler import CourseCrawler
        crawler = app.crawler or CourseCrawler(browser=browser, cfg=app.cfg, store=app.store)
        app.crawler = crawler
    from exam.guard import ExamGuard
    guard = ExamGuard(app.cfg, app.store)
    app.need_ai()
    runner = DiscussionRunner(browser=browser, cfg=app.cfg, store=app.store, crawler=crawler, engine=app.engine)
    page = browser.start().page
    if page is None:
        err("浏览器页未就绪。")
        return 3
    if getattr(args, "dump", False):
        paths = runner.capture(page, course)
        ok("讨论区结构已导出(只读)：" + "; ".join(paths))
        info("把这些文件路径发我，即可按真实 markup 适配 disc_* 选择器。")
        return 0
    # 先看成绩里是否有“计分讨论”(讨论只处理计分项)。
    targets = graded_discussion_targets(app.store.list_grades(int(course["id"])))
    if not targets:
        warn("成绩里没有“计分的讨论”项;先 `python main.py grades` 抓取成绩,且仅处理计分讨论。")
        return 0
    ok("讨论目标：" + "; ".join(f"{t['name']}(占{t['weight']:g}%)" for t in targets))
    # 进入讨论区,枚举话题并生成 AI 草稿(此步只读,绝不发布)。
    runner.open_board(page, course)
    replied = app.store.replied_topic_keys(int(course["id"]))
    kind_map = {"post": "new_post", "reply": "reply", "all": None}
    kind = kind_map.get(str(getattr(args, "kind", "all") or "all"), None)
    kind_label = {"new_post": "发表（提问）", "reply": "回复（约150字）", None: "发表+回复"}[kind]
    if int(getattr(args, "max", 0) or 0) > 0:
        app.cfg.values["DISCUSS_MAX"] = int(args.max)      # 本次运行覆盖每轮上限(防刷屏值可调)
    drafts, summary = runner.prepare_drafts(page, course, replied, kind=kind)
    if summary:
        info(summary + f"｜模块：{kind_label}｜每轮上限：{app.cfg.get('DISCUSS_MAX')}")
    est = getattr(runner, "last_estimate", None) or {}
    if int(est.get("rec") or 0) > int(app.cfg.get("DISCUSS_MAX") or 5):
        info(f"提示：按剩余分推荐每轮上限 {est['rec']} 条，可加 `--max {est['rec']}` 一次凑满。")
    if not drafts:
        warn("没有可生成的草稿(可能该模块本轮无任务、已达目标、话题为空、或 AI 未启用/返回空)。")
        return 0
    print(f"\n—— AI 讨论草稿预览（共 {len(drafts)} 条，未发布）——")
    for i, d in enumerate(drafts, 1):
        tag = "发帖·提问" if d["type"] == "new_post" else f"回复《{d['title'][:24]}》"
        print(f"\n[{i}] {tag}\n{d['text']}")
    can, why = discuss_write_allowed(app.cfg, guard, str(page.url or ""))
    if not getattr(args, "fill", False) and not getattr(args, "submit", False):
        info("\n只读预览完成。`discuss --fill` 半自动填入(你点发表)；`discuss --submit` 全自动填入并由程序点发表/回复(需开 PRACTICE_ALLOW_DISCUSS_SUBMIT)。")
        return 0
    if not can:
        warn("不会自动填入：" + why)
        return 0

    if getattr(args, "submit", False):
        from course.discussion import discuss_submit_allowed, publish_batch_preview
        import time as _t
        can_s, why_s = discuss_submit_allowed(app.cfg, guard, str(page.url or ""))
        if not can_s:
            warn("不会自动提交：" + why_s)
            return 0
        cid = int(course["id"])
        unresolved = [r for r in app.store.list_discussions(cid, limit=10000) if r["status"] == "pending_verify"]
        if unresolved:
            warn("存在上次发布结果待核验的记录。请在工作台对应模块核验并点“确认已发布”后继续。")
            return 0
        info("\n" + publish_batch_preview(course, drafts))
        if not confirm(f"以当前账号发布以上 {len(drafts)} 条讨论？"):
            return 0
        posted = 0
        for i, d in enumerate(drafts, 1):
            can_s, why_s = discuss_submit_allowed(app.cfg, guard, str(page.url or ""))
            if not can_s:
                warn(why_s + "，本轮停止。")
                break
            row = app.store.q1("SELECT status FROM discussions WHERE course_id=? AND fp=?", (cid, d["fp"]))
            if row is not None and row["status"] == "posted":
                continue
            if row is not None and row["status"] == "pending_verify":
                warn("该条发布结果待核验，本轮停止。")
                break
            if row is not None and row["status"] == "draft":
                warn("该条草稿此前已填入但未确认是否发布，本轮停止；请先核验或放弃旧草稿。")
                break
            kind = d["type"]
            tag = "发帖" if kind == "new_post" else f"回复《{d['title'][:20]}》"
            title = ""
            if kind == "new_post":
                if not runner.open_new_post(page):
                    warn("未能打开新建话题表单，本轮停止。")
                    break
                _t.sleep(1.2)
                title = (d.get("title") or d["text"][:20]).strip()
            elif not runner.open_topic_reply(page, d.get("topic_key", "")):
                warn(f"  [{i}] {tag}：未能点开回复框，本轮停止 → 请手动打开目标话题。")
                break
            target_page = runner.reply_page(page) if kind == "reply" else page
            if target_page is None:
                warn(f"  [{i}] {tag}：目标话题页或回复框已变化，未填入或提交。")
                break
            res = runner.fill_editor(target_page, d["text"], title=title)
            if not res.get("body"):
                warn(f"  [{i}] {tag}：没找到可见编辑器，请手动点开后用 --fill。可直接复制：\n{d['text']}")
                break
            if kind == "new_post" and not res.get("title"):
                warn("标题未填入，本轮停止，请手动核对。")
                break
            app.store.mark_discussed(cid, kind, d.get("topic_key", ""), d["fp"], d.get("title", ""), status="draft")
            warn(f"  ⚠ 即将以你的账号自动提交{tag}到讨论区（公开、不可撤回）")
            app.store.update_discuss_status(cid, d["fp"], "pending_verify")
            state, why2 = runner.publish(target_page, kind, d["text"], guard)
            app.store.update_discuss_status(cid, d["fp"], state)
            if state == "posted":
                posted += 1
                ok(f"  [{i}] {tag}：发布已核验 ✓")
                _t.sleep(1.2)
            else:
                warn(f"  [{i}] {tag}：{why2}，本轮停止。")
                if confirm("你已在页面确认该条发布成功？"):
                    app.store.confirm_discuss_published(cid, d["fp"])
                    posted += 1
                break
        info(f"讨论本轮结束：确认发布 {posted}/{len(drafts)} 条。")
        return 0

    def _pause(msg: str) -> str:
        try:
            return input(msg).strip().lower()
        except EOFError:
            return "q"

    # 选一条(--pick)或全部顺序走一遍;浏览器保持打开、不重新导航,回复框不会被关掉。
    pick = int(getattr(args, "pick", 0) or 0)
    if pick < 0 or pick > len(drafts):
        warn(f"--pick 应为 0（全部）或 1～{len(drafts)}。")
        return 2
    todo = [drafts[pick - 1]] if pick else drafts
    info("\n半自动：程序只把草稿【填入编辑器】，“发表/回复”由你在页面亲自点击。输入 q 随时结束。\n")
    for i, d in enumerate(todo, 1):
        if d["type"] == "new_post":
            runner.open_new_post(page)
            title = (d["title"] or d["text"][:20]).strip()
            ready = _pause(f"[{i}/{len(todo)}] 发帖：已尝试打开“新建话题”表单。请在页面点开正文编辑器后按回车填入（q 结束）：")
        else:
            title = ""
            ready = _pause(f"[{i}/{len(todo)}] 回复《{d['title'][:24]}》：请先在页面点开这条话题、并点到“回复”输入框，然后按回车填入（q 结束）：")
        if ready == "q":
            break
        if app.store.is_discussed(int(course["id"]), d["fp"]):
            warn("这条内容已有本地记录，未再填入；请先核验或重新生成草稿。")
            break
        can, why = discuss_write_allowed(app.cfg, guard, str(page.url or ""))
        if not can:
            warn(why)
            break
        if d["type"] == "reply" and not runner.open_topic_reply(page, d.get("topic_key", "")):
            warn("未能确认目标话题的回复页，未填入；请打开目标话题后重试。")
            break
        target_page = runner.reply_page(page) if d["type"] == "reply" else page
        if target_page is None:
            warn("目标话题页或回复框已变化，未填入回复。")
            break
        res = runner.fill_editor(target_page, d["text"], title=title)
        if res["body"]:
            if not app.store.mark_discussed(int(course["id"]), d["type"], d.get("topic_key", ""),
                                             d["fp"], d.get("title", ""), status="draft"):
                warn("这条内容已有本地记录，请勿重复发布。")
                break
            ok(f"已填入（正文{'＋标题' if res['title'] else '，标题未填'}）。请核对后【手动点发表/回复】。")
            ans = _pause("  请在页面核验，发布成功输入 y；未发布且放弃输入 s；q 结束并保留草稿：")
            if ans == "s":
                app.store.exec("DELETE FROM discussions WHERE course_id=? AND fp=? AND status='draft'", (int(course["id"]), d["fp"]))
                warn("  已撤销该条记录。")
            elif ans in ("y", "yes", "是"):
                app.store.confirm_discuss_published(int(course["id"]), d["fp"])
                ok("  已按你的确认记录为已发布。")
            else:
                warn("  未确认发布，保留草稿并结束。")
                break
        else:
            warn(f"  未找到可见编辑器，请确认已点开【{('发帖正文' if d['type']=='new_post' else '回复输入框')}】。可直接复制：\n{d['text']}")
    ok("讨论半自动流程结束。所有“发表/回复”均由你本人完成。")
    return 0


def cmd_status(app: App) -> int:
    banner("学习状态")
    stats = app.store.stats()
    info(f"课程 {len(app.store.list_courses())} 门 ｜ 学习项 {stats.get('total', 0)} ｜ "
         f"已完成 {stats.get('finished', 0)} ｜ 未完成 {stats.get('remaining', 0)}")
    info(f"累计真实学习时长：{fmt_duration_text(float(stats.get('watch_sec') or 0))}")
    info(f"题库 {stats.get('questions')} 题 ｜ 错题 {stats.get('wrong')} 条（累计错 {stats.get('wrong_total')} 次）")
    info(f"知识库 {stats.get('kb_docs')} 份已完成资料 / {stats.get('kb_chunks')} 个知识块")
    for c in app.store.list_courses()[:12]:
        s = app.store.stats(int(c["id"]))
        total = int(s.get("total") or 0)
        done = int(s.get("finished") or 0)
        pct = (done / total * 100) if total else 0
        info(f"  · {c['name'][:28]:<30} {done}/{total}  {pct:5.1f}%")
    section("安全红线（不可配置）")
    print(SAFETY_TEXT())
    return 0


def cmd_dump(app: App, args: argparse.Namespace) -> int:
    banner("导出页面结构（用于选择器排障）")
    browser = app.need_browser()
    if not browser.wait_for_manual_login():
        return 2
    page = browser.start().page
    from browser.actions import safe_goto

    if args.url:
        safe_goto(page, args.url)
    path = browser.dump_html(page, args.tag or "dump")
    ok(f"已导出：{path}")
    info("把其中稳定的 class/id 追加到 user_config.py 的 SELECTOR_OVERRIDES 即可适配新版页面。")
    return 0


# ------------------------------------------------------------------ 自动发现课程页面
def _scan_course_links(app: App):
    """扫描当前浏览器所有页签的所有 frame，找出带 courseId 的课程链接。"""
    from browser.js import COLLECT_LINKS_JS

    browser = app.need_browser()
    found = {}
    for page in browser.tabs() or ([browser.start().page] if browser.page else []):
        try:
            frames = list(page.frames)
        except Exception:
            frames = []
        for frame in frames:
            try:
                rows = frame.evaluate(COLLECT_LINKS_JS, {"groupSelectors": []}) or []
            except Exception:
                continue
            for r in rows:
                href = str(r.get("url", ""))
                low = href.lower()
                if "courseid" not in low and "cpi=" not in low:
                    continue
                if not href.startswith("http"):
                    continue
                text = " ".join(str(r.get("text", "")).split())[:60]
                if href not in found and text:
                    found[href] = text
        try:
            url = page.url
        except Exception:
            url = ""
        if url and ("courseid" in url.lower() or "studycourse" in url.lower()):
            found.setdefault(url, "（当前页面本身）")
    return found


# ------------------------------------------------------------------ 作业 / 测验
PLEDGE_RE = re.compile(r"(在线学习诚信承诺书|诚信承诺|我已知晓|我已完整知晓|我承诺|自愿遵守|我已阅读并同意)")


def pledge_pending(page) -> str:
    """检测页面是否还停在“诚信承诺书/知情同意”弹窗上（只读检测，绝不代签）。"""
    try:
        text = " ".join((page.inner_text("body") or "")[:6000].split())
    except Exception:
        return ""
    m = PLEDGE_RE.search(text)
    return m.group(0) if m else ""


def require_pledge_signed(app: App, page, what: str) -> bool:
    """
    学习通课程常要求本人签署《在线学习诚信承诺书》。
    检测到未签署时：停止自动操作，提示由你本人点击同意（程序绝不代点）。
    """
    hit = pledge_pending(page)
    if not hit:
        return True
    warn(f"检测到《{hit}》等承诺/知情文本仍未确认。")
    info("该承诺必须由**你本人**阅读并点击同意，程序不会代你签署，也不会绕过该弹窗。")
    info(f"请你在浏览器窗口里自己同意后，再重新运行：{what}")
    return False


ENTRY_TABS = {"work": ("作业", "本班级作业", "我的作业"),
              "exam": ("考试", "测验", "作业/考试")}


# ------------------------------------------------------------------ 直接问 AI
def _read_multiline(prompt: str) -> str:
    """读取多行题目（连续空行或 Ctrl+Z 结束）。"""
    print(prompt)
    lines: list[str] = []
    blank = 0
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line.strip():
            blank += 1
            if blank >= 2:
                break
            lines.append(line)
            continue
        blank = 0
        lines.append(line)
    return "\n".join(lines).strip()


def cmd_ask(app: App, text: str = "", course_key: str = "", save: bool = True, top: int = 6) -> int:
    """
    把题目贴给 AI 解析：输出【答案】【解析】【知识点】【资料依据】【可信程度】。
    这是“你自己学习时问问题”的工具：不接触任何答题页面，也不替你提交任何东西。
    """
    from questions.models import Question, normalize_kind
    from ui import report

    banner("AI 题目解析（自主练习用）")
    raw = (text or "").strip()
    if not raw and getattr(app.cfg, "stdin_paste", True):
        raw = _read_multiline("请粘贴题目（题干+选项，可多行；连按两次回车结束）：")
    if not raw:
        err("没有输入题目内容。用法：python main.py ask \"题干 选项A 选项B\" 或 python main.py ask --file 题目.txt")
        return 2
    if len(raw) < 6:
        err("题目内容太短，无法解析。")
        return 2

    course = app.pick_course(course_key) if course_key else None
    cid = (course or {}).get("id")
    q = Question.from_raw({"stem": raw, "kind": "unknown", "no": ""})
    q.kind = normalize_kind(q.kind, [raw[:80]])
    info(f"识别题型：{q.kind_label}（若不对，可在题目开头加“（多选题）”等字样）")

    kb = app.need_kb()
    evidence = {"chunks": [], "questions": [], "wrongs": []}
    try:
        ev = kb.evidence_for_question(q.as_dict(), course_id=cid, top_k=top)
        evidence = {"chunks": list(ev.get("chunks") or []), "questions": list(ev.get("questions") or []),
                    "wrongs": list(ev.get("wrongs") or [])}
        info(f"本地资料命中 {len(evidence['chunks'])} 条 ｜ 历史题 {len(evidence['questions'])} ｜ 错题 {len(evidence['wrongs'])}")
    except Exception as exc:
        log.warning("本地检索失败：%s", exc)

    app.need_ai()
    if not app.engine or not app.ai or not app.ai.enabled:
        warn("未配置 AI 密钥（user_config.py 的 AI_API_KEY），只能给本地资料，不能生成答案。")
        print(report.render_evidence(evidence["chunks"], title="本地资料"))
        info("配置方法：编辑 user_config.py → AI_API_KEY / AI_BASE_URL / AI_MODEL，然后重跑本命令。")
        return 3
    ans = app.engine.answer({**q.as_dict(), "kind": q.kind}, evidence=evidence, mode="practice",
                            course=(course or {}).get("name", ""), qno="1")
    print("\n" + report.render_question(q, 1, 1))
    print("\n" + report.render_answer(ans))
    print("\n" + report.render_evidence(evidence["chunks"], title="课程依据（本地资料）"))
    print("\n" + report.render_wrong_refs(evidence["wrongs"]))
    if ans.needs_human:
        warn("该题被判定为“需要人工确认”，请自己核对（常见原因：题干含图片、条件不足、本地无依据）。")
    if save:
        qid, dup = app.store.upsert_question(q.to_row(), course_id=cid)
        app.store.set_question_ai_result(qid, answer=ans.answer, analysis=ans.analysis,
                                         knowledge=ans.knowledge, answer_source="ai")
        info(f"已存入本地题库（{'重复题，已合并' if dup else '新题'}，id={qid}）；"
             f"后续可用 python main.py wrong drill 复习")
    return 0


def cmd_exercises(app: App, needle: str = "", entry: str = "work", index: int = 0,
                  limit: int = 20) -> int:
    """
    进入“作业/章节测验”列表 → 勾选一条 → 打开答题页 → 交给 practice 流程
    （AI 只分析，作答与提交由你本人完成）。
    """
    import time as _t

    from browser.actions import safe_goto
    from course.crawler import TASK_LIST_JS, CourseCrawler
    from questions.extractor import QuestionExtractor

    banner(f"作业/测验入口（{entry}）",
           "只读浏览 → 提取题目 → AI 分析；作答与提交由你本人在学习通页面完成。")
    if entry == "exam":
        warn("注意：若这是**正式考试**（期末考试等），本命令只做“读题概览”，不会调用 AI 答题；")
        warn("     考试 AI 辅助请使用：python main.py exam --course 课程名（需通过三重合规确认）。")
    course = app.pick_course(needle)
    if not course:
        return 2
    if not confirm_action("打开作业/测验列表",
                          [f"课程：{course['name']}", f"入口：{'作业' if entry == 'work' else '考试/测验'}",
                           "动作：只读取页面内容，不勾选、不填写、不提交"], skip=True):
        return 4
    browser = app.need_browser()
    if not browser.wait_for_manual_login():
        return 2
    page = browser.start().page
    crawler = CourseCrawler(browser=browser, cfg=app.cfg, store=app.store)
    safe_goto(page, course["url"], attempts=2)
    _t.sleep(3)
    if not crawler._open_nav_tab(page, ENTRY_TABS.get(entry, ENTRY_TABS["work"])):
        err("没找到“作业/考试”标签。请在浏览器里手动点进该页面，再用 practice --url 指定地址。")
        browser.dump_html(page, "exercises_no_tab")
        return 3
    data = {}
    for _ in range(12):                       # 轮询等待列表渲染
        for fr in page.frames:
            got = A_js(fr, TASK_LIST_JS)
            if got and got.get("items"):
                data = got
                break
        if data:
            break
        _t.sleep(2.5)
    items = data.get("items") or []
    if not items:
        err("列表为空或结构未识别。已导出页面结构：data/dumps/ 下最新文件。")
        browser.dump_html(page, "exercises_no_item")
        return 3
    section(f"共 {len(items)} 条（{data.get('title','')[:24]}）")
    for i, it in enumerate(items[: limit if limit else len(items)], 1):
        info(f"{i}. {it['text'][:70].replace(chr(10), ' / ')}")
        if it.get("status") or it.get("score"):
            info(f"     状态：{it.get('status') or '-'}  得分：{it.get('score') or '-'}")
    pick = index
    if not pick:
        if not _is_tty():
            err("未用 --index 指定条目，且当前不是交互终端，已停止（不会替你乱选）。")
            return 4
        raw = ask("输入要打开的序号（回车取消）")
        if not raw.isdigit():
            return 4
        pick = int(raw)
    if not (1 <= pick <= len(items)):
        err("序号超出范围")
        return 4
    target = items[pick - 1]
    ok(f"打开第 {pick} 条：{target['text'][:50]}")
    opened = False
    href = str(target.get("href") or "")
    if href.startswith("http"):        # javascript: 之类的伪链接不能当地址用
        opened = safe_goto(page, href, attempts=2)
    if not opened:
        # 列表项常是 JS 跳转：直接点它
        for fr in page.frames:
            try:
                loc = fr.get_by_text(target["text"][:20], exact=False).first
                if loc.count():
                    loc.scroll_into_view_if_needed(timeout=3000)
                    loc.click(timeout=4000)
                    opened = True
                    break
            except Exception:
                continue
    if not opened:
        err("无法打开该条目")
        return 3
    _t.sleep(4)
    # 学习通答题前常有“知情同意/承诺书”：必须由你本人同意，程序绝不代点
    if not require_pledge_signed(app, page,
                                 f"python main.py exercises --course {needle} --entry {entry} --index {pick}"):
        hold_browser(app)
        return 5
    if pledge_pending(page) and not QuestionExtractor(app.cfg).extract(page):
        warn("该作业需要由你本人同意“知情同意/承诺书”后才能进入答题页（程序不代你点同意）。")
        info("请在浏览器窗口里自己点击“同意 / 开始学习”，然后重跑：")
        info(f"    python main.py exercises --course {needle} --entry {entry} --index {pick}")
        hold_browser(app)
        return 5
    if entry == "exam":
        qs = QuestionExtractor(app.cfg).extract(page)
        info(f"（考试/测验入口，只读概览）识别到 {len(qs)} 道题；需要 AI 辅助请走 exam 模式（合规确认）")
        for q in qs[:5]:
            info(f"  · [{q.kind_label}] {q.preview(60)}")
        hold_browser(app)
        return 0
    runner = app.need_practice()
    runner.run(course, chapter_key="", limit=limit or 20, page=page, ask_import=True)
    hold_browser(app)
    return 0


def A_js(frame, script):
    """在 frame 里执行只读 JS（异常返回 None）。"""
    try:
        return frame.evaluate(script)
    except Exception:
        return None


def cmd_watch(app: App, seconds: int = 180, hold: bool = True) -> int:
    """
    自动发现课程：在你登录并浏览课程的过程中持续扫描所有页签，
    一旦发现课程（链接或当前页面 URL 带 courseId）就立刻入库；
    全程保持浏览器打开，结束时按回车才关闭（避免“突然就关了”）。
    """
    import time as _t

    from course.crawler import _query
    from course.models import Course

    banner("自动发现课程（持续收集）",
           f"请在浏览器里：登录 → 点进『我的课程』→ 逐个点开你在学的课程。\n"
           f"程序每 4 秒扫描一次，最长 {seconds} 秒；期间浏览器不会被关闭。")
    browser = app.need_browser()
    if not browser.wait_for_manual_login():
        err("未检测到登录状态：请先由你本人完成登录。")
        return 2
    ok("登录已确认，开始监听页面。浏览器会一直保持打开，你可以慢慢点课程。")
    collected: dict[str, Course] = {}
    # 先被动读取空间自身的课程数据（通常一次就能拿到全部在学课程）
    for key, c in harvest_courses(app, name="", seconds=25).items():
        if key in collected:
            continue
        c.id = app.store.upsert_course(c.to_dict())
        collected[key] = c
        ok(f"发现课程：{c.name}  courseId={c.course_key}")
    deadline = _t.time() + max(30, int(seconds))
    while _t.time() < deadline:
        links = _scan_course_links(app)
        fresh = 0
        for href, text in links.items():
            ids = _query(href, "courseId", "cpi", "clazzid")
            cid = ids.get("courseId") or ids.get("id") or ""
            if not cid:
                continue
            key = Course.make_key(cid, ids.get("cpi", ""), ids.get("clazzid", ""))
            if key in collected:
                continue
            course = Course(course_key=key, name=text or f"课程{cid}", url=href,
                            cpi=ids.get("cpi", ""), clazzid=ids.get("clazzid", ""))
            course.id = app.store.upsert_course(course.to_dict())
            collected[key] = course
            fresh += 1
            ok(f"新发现课程：{course.name}  (courseId={cid}, cpi={course.cpi})")
        if fresh:
            info(f"累计已发现 {len(collected)} 门课程")
        try:
            cur = browser.page.url if browser.page else ""
        except Exception:
            cur = ""
        info(f"扫描中…剩余 {int(deadline - _t.time())} 秒 ｜ 当前页面：{cur[:70]}")
        _t.sleep(4)
    if not collected:
        err("没有发现课程。请确认已登录，并在浏览器里点开任意一门课程的章节页面后重试。")
    else:
        section("发现结果")
        for c in sorted(collected.values(), key=lambda x: x.name):
            info(f"[{c.id}] {c.name}")
        info("下一步：python main.py sync --course 课程名   （抓章节目录）")
    if hold:
        print("\n" + "=" * 62)
        info("浏览器保持打开中。要继续操作请在自己的终端运行 `python main.py menu`；")
        info("按回车键关闭浏览器并结束本命令（登录 Cookie 是会话级的，关闭后需重新登录）。")
        print("=" * 62)
        try:
            if sys.stdin and sys.stdin.isatty():
                input()
            else:
                warn("非交互终端：将在 5 秒后自动关闭浏览器。")
                _t.sleep(5)
        except (EOFError, KeyboardInterrupt):
            print()
    return 0 if collected else 3


# ------------------------------------------------------------------ 勾选确认工具
def _is_tty() -> bool:
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        return False


def choose_course_interactive(app: "App", needle: str = "") -> dict[str, Any] | None:
    """
    选择课程：匹配到多门时必须由你勾选，程序不会替你猜。

    返回课程字典；无法确定（或你取消）时返回 None。
    """
    courses = app.store.list_courses()
    if not courses:
        warn("本地还没有课程数据。先运行：python main.py pick --course 课程名  或  python main.py watch")
        return None
    if not needle:
        section("请选择课程（输入序号勾选；程序不会自动替你选）")
        for i, c in enumerate(courses, 1):
            info(f"{i}. {c['name']}   (course_key={c['course_key']})")
        if not _is_tty():
            err("当前不是交互终端，无法勾选。请用 --course <课程名或序号> 指定。")
            return None
        needle = ask("输入序号或课程名（回车取消）")
        if not needle:
            warn("已取消，未选择任何课程。")
            return None
    matched = []
    if str(needle).isdigit():
        nid = int(needle)
        matched = [c for c in courses if c["id"] == nid or courses.index(c) + 1 == nid]
    if not matched:
        key = str(needle).strip().lower()
        matched = [c for c in courses
                   if key in str(c.get("name", "")).lower() or key in str(c.get("course_key", "")).lower()]
    if len(matched) == 1:
        ok(f"已选定课程：{matched[0]['name']}（course_key={matched[0]['course_key']}）")
        return matched[0]
    if not matched:
        err(f"没有课程匹配『{needle}』。本地已有课程：")
        for c in courses:
            info(f"  [{c['id']}] {c['name']}")
        return None
    section(f"关键词『{needle}』匹配到 {len(matched)} 门课程，请勾选一门")
    for i, c in enumerate(matched, 1):
        info(f"{i}. {c['name']}   (course_key={c['course_key']})")
    if not _is_tty():
        err("非交互终端无法勾选，已停止（不会自动选第一门）。请用 --course 序号 精确指定。")
        return None
    raw = ask("输入序号勾选（回车取消）")
    if raw.isdigit() and 1 <= int(raw) <= len(matched):
        c = matched[int(raw) - 1]
        ok(f"你已勾选：{c['name']}")
        return c
    warn("输入无效，未选择任何课程。")
    return None


def confirm_action(title: str, lines: list[str], skip: bool = False) -> bool:
    """执行前的勾选确认：把“要做什么”列清楚，必须你确认才动手。"""
    section(f"请确认：{title}")
    for ln in lines:
        info(ln)
    if skip:
        warn("使用了 --yes，已跳过确认。")
        return True
    if not _is_tty():
        err("当前不是交互终端：为避免误操作已停止。请在自己的终端运行，或加 --yes 明确跳过。")
        return False
    return confirm("确认执行？")


ID_PATTERNS = {
    "courseId": [r"courseId['\"=:\s]+([0-9]{5,})", r"courseid['\"=:\s]+([0-9]{5,})",
                 r"course_?id['\"=:\s]+([0-9]{5,})"],
    "cpi": [r"cpi['\"=:\s]+([0-9]{5,})"],
    "clazzid": [r"clazzid['\"=:\s]+([0-9]{4,})"],
}


def _extract_ids(text: str) -> dict[str, str]:
    """从 URL 或页面源码里挖出 courseId / cpi / clazzid。"""
    out: dict[str, str] = {}
    blob = str(text or "")
    for key, pats in ID_PATTERNS.items():
        for pat in pats:
            m = re.search(pat, blob, re.I)
            if m:
                out[key] = m.group(1)
                break
    return out


def _course_from_page(app: App, name: str):
    """在浏览器现有页签里找“已打开的课程页”，返回 (Course, 依据说明)。"""
    from course.models import Course

    browser = app.need_browser()
    pages = browser.tabs()
    for page in list(pages):
        try:
            url = str(page.url or "")
            title = " ".join(str(page.title() or "").split())
        except Exception:
            continue
        if name and (name not in title) and (name not in url):
            continue                       # 不是目标课程页，绝不乱选
        ids = _extract_ids(url)
        basis = f"url:{url[:70]}"
        if "courseId" not in ids:
            try:
                html = page.content()[:400000]
            except Exception:
                html = ""
            ids2 = _extract_ids(html)
            if ids2.get("courseId"):
                ids = {**ids2, **ids}
                basis = f"页面源码（标题：{title[:40]}）"
        cid = ids.get("courseId") or ids.get("cpi")
        if not cid:
            continue
        course = Course(course_key=Course.make_key(cid, ids.get("cpi", ""), ids.get("clazzid", "")),
                        name=title or name, url=url, cpi=ids.get("cpi", ""),
                        clazzid=ids.get("clazzid", ""))
        return course, basis
    return None, ""


# ------------------------------------------------------------------ 自动进入指定课程
PICK_CLICK_JS = r"""
(name) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const all = Array.prototype.slice.call(document.querySelectorAll('[aria-label],[title],a,li,h3,h4,div,span,p'));
  const hit = all.filter((el) => {
    const a = norm(el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('title')));
    const t = norm(el.innerText);
    return (a && a.indexOf(name) >= 0) || t === name;
  });
  if (!hit.length) return {ok: false, count: 0};
  const pointer = (el) => { try { return getComputedStyle(el).cursor === 'pointer'; } catch (e) { return false; } };
  let best = hit.find((el) => el.tagName === 'A' || (el.getAttribute && el.getAttribute('onclick')))
          || hit.find(pointer) || hit[0];
  let target = best;
  for (let i = 0; i < 5 && target.parentElement; i++) {
    const par = target.parentElement;
    if ((par.getAttribute && par.getAttribute('onclick')) || pointer(par) || par.tagName === 'A') { target = par; break; }
    target = par;
  }
  try { target.scrollIntoView({block: 'center'}); } catch (e) {}
  const info = {ok: true, count: hit.length, tag: target.tagName,
                cls: (target.className || '').toString().slice(0, 90),
                text: norm(best.innerText || best.getAttribute('aria-label')).slice(0, 60),
                frame: location.href.slice(0, 90)};
  try { target.click(); } catch (e) { info.clickError = String(e); }
  return info;
}
"""


def _try_open_course(app: App, name: str) -> dict:
    """
    在个人空间里找到该课程卡片并**用真实鼠标事件**点开（等价于你自己点一下）。

    学习通空间里的课程卡片是 Vue 组件，只监听真实指针事件，
    因此优先用 Playwright 的 Locator.click()（会滚动到可见处并按坐标点击），
    失败时才退回 JS 合成点击。
    """
    browser = app.need_browser()
    result = {"clicked": False, "detail": {}}
    css = ", ".join([f'[aria-label*="{name}"]', f'[title="{name}"]', f'[title*="{name}"]'])
    for page in browser.tabs():
        for frame in list(page.frames):
            try:
                loc = frame.locator(css).first
                if loc.count() == 0:
                    continue
                loc.scroll_into_view_if_needed(timeout=4000)
                box = loc.bounding_box(timeout=3000)
                # 点文字块常无效：往上找可点击容器（cursor:pointer / onclick / a）
                target = loc
                for up in range(1, 5):
                    parent = frame.locator(css).first.locator("xpath=" + "/".join([".."] * up))
                    try:
                        if parent.count() == 0:
                            break
                        style = parent.evaluate(
                            "el => ({cursor: getComputedStyle(el).cursor, onclick: !!el.getAttribute('onclick'),"
                            " tag: el.tagName})") or {}
                        if style.get("cursor") == "pointer" or style.get("onclick") or style.get("tag") == "A":
                            target = parent
                            break
                    except Exception:
                        break
                try:
                    target.click(timeout=5000)
                except Exception:
                    if box:
                        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    else:
                        raise
                result["clicked"] = True
                result["detail"] = {"text": name, "tag": "locator-click", "frame": str(frame.url)[:80]}
                app.store.log_study(None, "pick_click", f"真实点击课程卡片：{name}", None)
                return result
            except Exception as exc:
                log.debug("Locator 点击失败：%s", exc)
                continue
    # 兜底：JS 合成点击（部分页面有效）
    for page in browser.tabs():
        for frame in list(page.frames):
            info = frame.evaluate(PICK_CLICK_JS, name) if frame else None
            if info and info.get("ok"):
                result["clicked"] = True
                result["detail"] = info
                app.store.log_study(None, "pick_click", f"JS 点击课程卡片：{name}", None)
                return result
    return result


def harvest_courses(app: App, name: str = "", seconds: int = 25) -> dict:
    """
    被动监听：个人空间加载时会请求自己的课程列表数据，
    从这些响应里读出 courseId/cpi/clazzid（只读观察页面自己的请求，不主动调用任何接口）。

    name 为空表示收集全部课程；返回 {course_key: Course}。
    """
    import json as _json
    import time as _t

    from course.models import Course

    browser = app.need_browser()
    ctx = getattr(browser, "context", None)
    if ctx is None:
        return None
    hits: dict[str, Course] = {}

    def on_response(resp):
        try:
            ct = (resp.headers or {}).get("content-type", "")
            if "json" not in ct and "text" not in ct:
                return
            url = resp.url
            if "chaoxing" not in url:
                return
            body = resp.text()
        except Exception:
            return
        if not body or "courseid" not in body.lower():
            return
        # 逐个 JSON 对象片段找“课程名 + courseId”
        for m in re.finditer(r"\{[^{}]{0,4000}?\}", body):
            seg = m.group(0)
            if name and name not in seg:
                continue
            ids = _extract_ids(seg)
            cid = ids.get("courseId")
            if not cid:
                continue
            title = name
            tm = re.search(r'"(?:name|courseName|title)"\s*:\s*"([^"]{2,60})"', seg)
            if tm:
                title = tm.group(1)
            key = Course.make_key(cid, ids.get("cpi", ""), ids.get("clazzid", ""))
            hits.setdefault(key, Course(course_key=key, name=title, url=url[:200],
                                        cpi=ids.get("cpi", ""), clazzid=ids.get("clazzid", "")))

    ctx.on("response", on_response)
    try:
        page = browser.page
        if page is not None:
            from browser.actions import safe_goto
            safe_goto(page, f"{(app.cfg.get('CHAOXING_HOME_URL') or 'https://i.chaoxing.com').rstrip('/')}/base",
                      attempts=1)
        deadline = _t.time() + max(8, int(seconds))
        while _t.time() < deadline and not hits:
            _t.sleep(1)
    finally:
        try:
            ctx.remove_listener("response", on_response)
        except Exception:
            pass
    return hits


def _harvest_course_data(app: App, name: str, seconds: int = 25):
    """按课程名收集；命中唯一才返回，多个候选必须你勾选（绝不自动选）。"""
    hits = harvest_courses(app, name=name, seconds=seconds)
    lst = list(hits.values())
    if len(lst) == 1:
        ok(f"从页面自身的课程数据中读到：{lst[0].name} courseId={lst[0].course_key}")
        return lst[0]
    if len(lst) > 1:
        section(f"课程数据里匹配到 {len(lst)} 门含『{name}』的课，请勾选")
        for i, c in enumerate(lst, 1):
            info(f"{i}. {c.name}  courseId={c.course_key}")
        if not _is_tty():
            err("非交互终端无法勾选，已停止（不会自动选）。")
            return None
        raw = ask("输入序号勾选")
        if raw.isdigit() and 1 <= int(raw) <= len(lst):
            return lst[int(raw) - 1]
    return None


def cmd_pick(app: App, name: str, do_sync: bool = True, seconds: int = 240,
           args_yes: bool = False, debug: bool = False) -> int:
    """按课程名自动进入课程页 → 抓 courseId/cpi → 入库 →（可选）同步章节目录。"""
    import time as _t

    from browser.actions import safe_goto
    from course.crawler import _query
    from course.models import Course

    banner(f"自动进入课程：{name}", "程序会在你的个人空间里找到该课程并点开（等价于你手动点一下），然后抓取课程地址。")
    browser = app.need_browser()
    if not browser.wait_for_manual_login():
        return 2
    page = browser.start().page
    safe_goto(page, f"{(app.cfg.get('CHAOXING_HOME_URL') or 'https://i.chaoxing.com').rstrip('/')}/base", attempts=2)
    _t.sleep(3)

    def _candidates():
        """只接受“标题里含课程名”的链接，避免误抓推荐课/广场课。"""
        out = {}
        for href, text in _scan_course_links(app).items():
            ids = _query(href, "courseId", "cpi", "clazzid")
            cid = ids.get("courseId") or ids.get("id") or ""
            if not cid:
                continue
            title = (text or "").strip()
            # 标题必须含课程名；但“点课后新页签的当前地址”没有标题文字，也认（因为我们点的就是这门课）
            if name and (name not in title) and title not in ("（当前页面本身）", ""):
                continue
            key = Course.make_key(cid, ids.get("cpi", ""), ids.get("clazzid", ""))
            out.setdefault(key, Course(course_key=key, name=title or name, url=href,
                                       cpi=ids.get("cpi", ""), clazzid=ids.get("clazzid", "")))
        return out

    target = None
    # 先被动读取空间自身的课程数据（最稳，且完全不触发跳转）
    found = _harvest_course_data(app, name, seconds=min(30, max(10, int(seconds) // 6)))
    if found:
        target = found
    deadline = _t.time() + max(30, int(seconds))
    for attempt in range(6 if target is None else 0):
        got = _try_open_course(app, name)
        if got["clicked"]:
            ok(f"已点击课程卡片：{got['detail'].get('text', name)[:40]}（{got['detail'].get('tag')}）")
        else:
            info(f"第 {attempt + 1} 次没在页面上找到『{name}』，刷新个人空间重试…")
            safe_goto(page, f"{(app.cfg.get('CHAOXING_HOME_URL') or 'https://i.chaoxing.com').rstrip('/')}/base", attempts=1)
            _t.sleep(3)
        # 等待跳转/新页签：先看链接，再从页面源码里挖 courseId/cpi（标题必须匹配课程名）
        for _ in range(30):
            cands = list(_candidates().values())
            if not cands:
                found, basis = _course_from_page(app, name)
                if found:
                    ok(f"从{basis} 取到课程地址")
                    target = found
                    break
            if len(cands) == 1:
                target = cands[0]
                break
            if len(cands) > 1:
                section(f"匹配到 {len(cands)} 个候选，请勾选（不会自动选）")
                for i, c in enumerate(cands, 1):
                    info(f"{i}. {c.name}  courseId={c.course_key}")
                if not _is_tty():
                    err("非交互终端无法勾选，已停止。")
                    return 4
                raw = ask("输入序号勾选目标课程（回车取消）")
                if raw.isdigit() and 1 <= int(raw) <= len(cands):
                    target = cands[int(raw) - 1]
                break
            _t.sleep(2)
        if target:
            break
        if _t.time() > deadline:
            break

    if not target:
        err(f"没能自动抓到『{name}』的课程地址。")
        if debug:
            section("现场诊断")
            for i, pg in enumerate(app.need_browser().tabs()):
                try:
                    info(f"[页签{i}] {pg.url[:120]}  ｜标题 {pg.title()[:40]}")
                except Exception:
                    info(f"[页签{i}] 无法读取")
            try:
                app.browser.dump_html(app.need_browser().page, "pick_debug")
            except Exception:
                pass
        info("请你在浏览器里**手动点开这门课**（停在章节目录页），然后运行：")
        info("    python main.py watch --seconds 120")
        return 3
    if not confirm_action("入库并同步该课程",
                          [f"课程：{target.name}", f"courseId/cpi：{target.course_key}",
                           f"地址：{target.url[:110]}",
                           f"同步章节目录：{'是' if do_sync else '否'}"], skip=args_yes):
        return 4
    target.id = app.store.upsert_course(target.to_dict())
    ok(f"已入库：{target.name}  courseId={target.course_key}")
    info(f"课程地址：{target.url[:130]}")
    if do_sync:
        cmd_sync(app, str(target.id))
    info(f"刷课命令：python main.py study --course {target.name}")
    return 0


# ------------------------------------------------------------------ 一键初始化
def _write_course_entry(url: str) -> bool:
    """把探测到的课程入口写回 user_config.py（带标记，可重复执行不叠加）。"""
    cfg_path = BASE_DIR / "user_config.py"
    if not cfg_path.exists():
        return False
    text = cfg_path.read_text(encoding="utf-8")
    block = (f"\n# >>> 由 setup 自动探测写入（可手动修改）\nCOURSE_LIST_URL = \"{url}\"\n"
             "# <<< 自动探测块结束\n")
    start, end = text.find("# >>> 由 setup"), text.find("# <<< 自动探测块结束")
    if start >= 0 and end > start:
        text = text[:start].rstrip() + "\n" + block.strip() + text[end + len("# <<< 自动探测块结束"):].lstrip("\n")
        text = text.rstrip() + "\n"
    else:
        text = text.rstrip() + "\n" + block
    cfg_path.write_text(text, encoding="utf-8")
    return True


def _find_course_entry(app: App) -> str:
    """在候选入口里找出能列出课程的那个（只读探测）。"""
    from browser.actions import safe_goto
    from browser.js import COLLECT_LINKS_JS

    page = app.need_browser().start().page
    assert page is not None
    candidates = [app.cfg.get("COURSE_LIST_URL")] + PROBE_URLS + [page.url]
    seen, best, best_n = set(), "", 0
    for url in [c for c in candidates if c]:
        if url in seen:
            continue
        seen.add(url)
        if not safe_goto(page, url, attempts=1):
            continue
        n = 0
        for frame in page.frames:
            rows = frame.evaluate(COLLECT_LINKS_JS, {"groupSelectors": []}) or []
            n += sum(1 for r in rows if "courseid" in str(r.get("url", "")).lower())
        info(f"探测 {page.url[:70]} → courseId 链接 {n} 条")
        if n > best_n:
            best, best_n = str(page.url), n
    return best if best_n else ""


def cmd_setup(app: App, needle: str = "") -> int:
    """一次性完成：登录 → 确定课程入口 → 同步课程 → 同步章节目录 → 汇总（全程不关浏览器）。"""
    banner("一键初始化（setup）", "登录 → 确定课程入口 → 同步课程 → 同步章节目录 → 汇总")
    browser = app.need_browser()
    if not browser.wait_for_manual_login():
        err("未检测到登录状态：请在弹出的浏览器窗口中由你本人完成登录与验证。")
        return 2
    ok("登录态已核验")
    entry = _find_course_entry(app)
    if entry and entry != app.cfg.get("COURSE_LIST_URL"):
        app.cfg.values["COURSE_LIST_URL"] = entry
        if _write_course_entry(entry):
            ok(f"课程入口已写入 user_config.py：{entry}")
        else:
            warn(f"建议手动设置 COURSE_LIST_URL = \"{entry}\"")
    elif not entry:
        warn("没有探测到可列出课程的入口页面，仍按默认地址尝试同步。")
    code = cmd_courses(app)
    course = app.pick_course(needle)
    if course is None:
        if not app.store.list_courses():
            err("课程列表为空，无法继续。请在学习通里打开“我的课程”页面后运行 `python main.py watch`。")
            return code or 3
        return 4
    code = cmd_sync(app, str(course["id"]), yes=True, hold=False) or code
    section("初始化完成，后续可以做什么")
    info(f"刷课：python main.py study --course {course['name']}")
    info(f"题库：python main.py practice --course {course['name']}")
    info(f"资料库：python main.py kb build --course {course['name']}")
    info(f"菜单（在自己的终端里运行，可连续操作）：python main.py menu")
    cmd_status(app)
    return code


# ------------------------------------------------------------------ 常驻会话菜单
PROBE_URLS = [
    "https://i.chaoxing.com/base",
    "https://i.chaoxing.com/",
    "https://v2_chapp.chaoxing.com/mooc2-ans/visit/studentstart.html",
    "https://mooc2-ans.chaoxing.com/mooc2-ans/course/studycourse",
    "https://courselist.chaoxing.com/course/getCourseList",
    "https://mooc1-1.chaoxing.com/visit/studentstart.html",
    "https://v2teach.chaoxing.com/visit/studentstart.html",
    "https://v2lpt.chaoxing.com/mooc2-ans/visit/studentstart.html",
]


def cmd_go(app: App, needle: str = "", limit: int = 0, kinds: str = "",
           practice: bool = False, skip_study: bool = False) -> int:
    """★日常就用这条：登录一次 → 同步章节 → 自动学习 → 汇总（全程一个进程，不重复登录）。"""
    banner("连续执行（go）", "登录一次 → 同步章节目录 → 自动学习 → 汇总；中途不会关闭浏览器")
    print(_safety_note())
    browser = app.need_browser()
    if not browser.wait_for_manual_login():
        err("未检测到登录状态：请在弹出的窗口里完成登录（只需这一次）。")
        return 2
    ok("登录态已确认并保存，后续命令不会再要求你登录")
    course = app.pick_course(needle)
    if not course:
        return 2
    if not confirm_action("同步并学习该课程",
                          [f"课程：{course['name']}",
                           "同步：只读取页面内容写入本地库",
                           "学习：视频 1.0 倍速真实播放；文档/PPT 真实停留",
                           "作答与提交：一律由你本人在学习通页面完成"], skip=True):
        return 4
    code = cmd_sync(app, str(course["id"]), yes=True, hold=False)
    if skip_study:
        cmd_status(app)
        hold_browser(app)
        return code
    ns = argparse.Namespace(course=str(course["id"]), limit=limit, kinds=kinds,
                            practice=practice, no_kb=False, all=False, yes=True)
    code = cmd_study(app, ns) or code
    cmd_status(app)
    hold_browser(app)
    return code


def cmd_probe(app: App) -> int:
    """探测哪个入口页能列出“我的课程”，并把结果写入 user_config 建议值。"""
    from browser.js import COLLECT_LINKS_JS

    banner("探测课程列表入口（只读，复用当前登录会话）")
    browser = app.need_browser()
    if not browser.wait_for_manual_login():
        return 2
    page = browser.start().page
    assert page is not None
    best = None
    for url in PROBE_URLS:
        from browser.actions import safe_goto

        if not safe_goto(page, url, attempts=1):
            info(f"× 打不开：{url}")
            continue
        total = courses = 0
        samples = []
        for frame in page.frames:
            rows = frame.evaluate(COLLECT_LINKS_JS, {"groupSelectors": []}) or []
            total += len(rows)
            for r in rows:
                href = r.get("url", "")
                if "courseId" in href or "courseid" in href:
                    courses += 1
                    if len(samples) < 3:
                        samples.append((r.get("text", "")[:26], href[:120]))
        flag = "√ 可用" if courses else "× 无课程链接"
        info(f"{flag} ｜ 链接 {total} 条 / courseId {courses} 条 ｜ {page.url[:80]}")
        for text, href in samples:
            print(f"        · {text} -> {href}")
        if courses and (best is None or courses > best[1]):
            best = (page.url, courses)
    if best:
        ok(f"建议把 user_config.py 的 COURSE_LIST_URL 改为：{best[0]}（命中 {best[1]} 条课程链接）")
        return 0
    err("所有候选入口都没有抓到课程链接：请在学习通里手动打开“我的课程”页，再用 dump 命令导出该页分析。")
    return 3


def _guided_start(app: App) -> dict[str, Any] | None:
    """按正确顺序引导：登录 → 自动检测课程 → 让你勾选当前课程（记住，后续不用再输）。"""
    browser = app.need_browser()
    if not browser.wait_for_manual_login():
        warn("未检测到登录：请在弹出的窗口中由你本人完成登录与验证。")
        return None
    ok("登录已确认")
    courses = app.store.list_courses()
    if not courses:
        section("① 自动检测课程")
        cmd_courses(app)
        courses = app.store.list_courses()
    current = app.store.get_course(app.store.get_meta("current_course", "")) if app.store.get_meta("current_course") else None
    if courses:
        section(f"② 选择当前课程（本地共 {len(courses)} 门）")
        for i, c in enumerate(courses, 1):
            mark = "  ← 当前" if current and c["id"] == current["id"] else ""
            info(f"{i}. {c['name']}{mark}")
        if len(courses) == 1 and current:
            return current
        if _is_tty():
            raw = ask("输入序号选择当前课程（回车沿用上次）")
            if raw.isdigit() and 1 <= int(raw) <= len(courses):
                c = courses[int(raw) - 1]
                app.store.set_meta("current_course", str(c["id"]))
                ok(f"当前课程：{c['name']}")
                return c
        elif current:
            return current
    return current


MENU_TEXT = """
  1 同步课程列表        2 同步章节目录        3 自动学习（刷课）
  4 刷题辅助            5 知识库建库          6 搜索（资料/题目/错题）
  7 错题库              8 学习状态            9 探测课程入口
  d 导出页面HTML        q 退出（关闭浏览器）
  ※ 浏览器在本菜单期间保持常开，登录态不会丢；学习通窗口请留在前台。"""


def cmd_menu(app: App) -> int:
    """常驻会话菜单：登录一次，后续所有操作在同一进程里完成。"""
    banner("学习通课程助手 · 交互菜单", MENU_TEXT)
    _guided_start(app)
    if not (sys.stdin and sys.stdin.isatty()):
        warn("当前不是交互终端，无法输入选择。请在自己的终端/PowerShell 窗口里运行 `python main.py menu`，"
             "或改用一键命令 `python main.py setup`。")
        return 0
    browser = app.need_browser()
    code = 0
    try:
        if not browser.wait_for_manual_login():
            err("未检测到登录状态：请在弹出的窗口中由你本人完成登录与验证。")
            return 2
        print(MENU_TEXT)
        while True:
            try:
                key = input("\n选择功能（输入 h 重看菜单，q 退出）> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if key == "h":
                print(MENU_TEXT)
                continue
            if not key:
                info("未输入内容。输入数字选择功能，h 看菜单，q 退出。")
                continue
            try:
                if key in ("q", "quit", "exit"):
                    break
                elif key == "1":
                    code = cmd_courses(app) or code
                elif key == "2":
                    needle = input("课程名或 id（回车用当前课程）：").strip() or _current_course_name(app)
                    code = cmd_sync(app, needle) or code
                elif key == "3":
                    ns = argparse.Namespace(course=_current_course_name(app),
                                            limit=_int_arg("本次上限(回车默认)"),
                                            kinds=input("只处理类型(如 video，回车全部)：").strip(),
                                            practice=input("遇练习进入刷题? y/N：").strip().lower() == "y",
                                            no_kb=False, all=False, yes=False, headless=False, keep=True)
                    code = cmd_study(app, ns) or code
                elif key == "4":
                    ns = argparse.Namespace(course=_current_course_name(app),
                                            url=input("练习页地址(回车用当前页)：").strip(),
                                            chapter="", limit=_int_arg("题量上限(回车默认50)") or 50,
                                            yes=False, headless=False, keep=True)
                    code = cmd_practice(app, ns) or code
                elif key == "5":
                    ns = argparse.Namespace(action="build", course=_current_course_name(app),
                                           dir="", limit=_int_arg("资料上限(回车默认50)") or 50, top=6, query=[])
                    code = cmd_kb(app, ns) or code
                elif key == "6":
                    q = input("关键词（如：戴维南定理 / 第二章电路分析 / 这个公式怎么用）：").strip()
                    if q:
                        ns = argparse.Namespace(query=[q], course=input("限定课程(回车不限)：").strip(),
                                                top=6, ai=input("让 AI 综合? y/N：").strip().lower() == "y")
                        code = cmd_search(app, ns) or code
                elif key == "7":
                    act = input("动作 list/stats/export/drill/similar：").strip()
                    _cur = _current_course_name(app)
                    if act in ("list", "stats", "export", "drill", "similar"):
                        ns = argparse.Namespace(action=act, course=input(f"课程名或 id（回车用 {_cur or '全部'}）：").strip() or _cur,
                                                limit=50, fmt="md", all=False, id=0, n=2)
                        code = cmd_wrong(app, ns) or code
                elif key == "9":
                    code = cmd_probe(app) or code
                elif key == "8":
                    code = cmd_status(app) or code
                elif key == "d":
                    ns = argparse.Namespace(url=input("页面地址：").strip(), tag="menu_dump")
                    code = cmd_dump(app, ns) or code
                else:
                    warn(f"无效选择：{key}（h 查看菜单，q 退出）")
            except KeyboardInterrupt:
                warn("已取消当前操作（Ctrl+C），回到菜单。")
            except Exception as exc:
                get_logger("main.menu").exception("菜单操作失败")
                err(f"操作失败：{exc}")
    finally:
        try:
            browser.stop()
        except Exception:
            log.debug("关闭浏览器异常", exc_info=True)
        ok("已退出菜单，浏览器关闭。学习进度均已保存在 data\study_helper.db。")
    return code


def _current_course_name(app: App) -> str:
    """取当前课程名（菜单里默认使用，避免反复手打）。"""
    cid = app.store.get_meta("current_course", "")
    if cid:
        c = app.store.get_course(cid)
        if c:
            return str(c["id"])
    return ""


def _int_arg(prompt: str) -> int:
    """读一个可选整数参数。"""
    raw = input(f"{prompt}：").strip()
    return int(raw) if raw.isdigit() else 0

# ------------------------------------------------------------------ 辅助
def _safety_note() -> str:
    return (
        "合规提醒：\n"
        "  · 登录、验证码、身份验证一律由你本人完成；\n"
        "  · 视频按 1.0 倍速正常播放，不加速、不跳进度、不伪造挂机状态；\n"
        "  · 练习题与考试：程序只读取题目并给出分析，作答与提交由你本人完成；\n"
        "  · 考试 AI 模式仅在课程明确允许时启用；页面若出现“禁止 AI/闭卷”会自动关闭；\n"
        "  · 不修改服务器数据、不修改倒计时、不绕过任何教师设置的限制。\n"
    )


def SAFETY_TEXT() -> str:
    return "\n".join(f"  · {k} = {v}" for k, v in SAFETY.items())


def fmt_duration_text(seconds: float) -> str:
    from utils.time_util import fmt_duration

    return fmt_duration(seconds)


def build_app() -> App:
    setup_logging(CONFIG.log_dir, CONFIG.get("LOG_LEVEL") or "INFO")
    for message in CONFIG.warnings:
        warn(message)
    from database.store import Store

    store = Store(db_path=CONFIG.db_path)
    return App(cfg=CONFIG, store=store)


def _parser() -> argparse.ArgumentParser:
    """命令行参数定义（每个子命令对应一个 cmd_* 函数）。"""
    p = argparse.ArgumentParser(prog="study_helper",
                                description="学习通课程学习 + 刷题 + 错题 + 开卷考试 AI 辅助"
                                             "（不带参数直接启动小窗）")
    # 不带任何参数 → 直接打开侧边栏小窗（默认启动方式）
    p.add_argument("--gui", action="store_true", help="启动侧边栏小窗（默认行为）")
    sub = p.add_subparsers(dest="cmd", required=False)

    sub.add_parser("gui", help="★小窗（小节工作台；默认启动方式，直接 python main.py 即可）")
    sub.add_parser("work", help="小窗（等价于 gui / 无参启动）")
    sub.add_parser("menu", help="命令行菜单（浏览器常驻，登录一次连续操作）")
    sub.add_parser("setup", help="一键完成 登录→同步课程→同步目录（非交互，适合首次使用）")

    sp = sub.add_parser("go", help="★日常推荐：登录一次→同步→自动学习（不重复登录）")
    sp.add_argument("--course", default="")
    sp.add_argument("--limit", type=int, default=0)
    sp.add_argument("--kinds", default="")
    sp.add_argument("--practice", action="store_true")
    sp.add_argument("--no-study", action="store_true", help="只同步不学习")
    sp.add_argument("--keep", action="store_true", help="结束后保持浏览器窗口不关闭")
    sp.add_argument("--headless", action="store_true", help="调试：不弹窗口（需已有登录 Cookie）")

    sp = sub.add_parser("ask", help="直接把题目贴给 AI：答案+解析+知识点+可信度+本地依据")
    sp.add_argument("text", nargs="*", help="题目内容（留空则交互式粘贴）")
    sp.add_argument("--course", default="")
    sp.add_argument("--top", type=int, default=6)
    sp.add_argument("--no-save", action="store_true", help="不写入本地题库")

    sp = sub.add_parser("exercises", help="进入作业/章节测验列表→勾选→提取题目→AI 分析")
    sp.add_argument("--course", default="")
    sp.add_argument("--entry", default="work", choices=["work", "exam"])
    sp.add_argument("--index", type=int, default=0, help="直接指定第几条（免交互）")
    sp.add_argument("--limit", type=int, default=20)

    sp = sub.add_parser("capture", help="抓取真实页面现场到 data/capture（一次登录后可离线分析）")
    sp.add_argument("--course", default="")
    sp.add_argument("--wait", type=int, default=40)
    sp.add_argument("--no-section", action="store_true", help="只抓章节页，不点进小节")

    sp = sub.add_parser("pick", help="按课程名自动进入该课程并同步目录（匹配多门时需你勾选）")
    sp.add_argument("--course", required=True, help="课程名关键词，如 改革开放史")
    sp.add_argument("--no-sync", action="store_true", help="只入库不同步目录")
    sp.add_argument("--seconds", type=int, default=240)
    sp.add_argument("--yes", action="store_true", help="跳过入库前确认（仅在唯一匹配时生效）")
    sp.add_argument("--debug", action="store_true", help="抓取失败时打印页签与页面现场")

    sp = sub.add_parser("watch", help="自动发现课程页面（浏览器全程保持打开）")
    sp.add_argument("--seconds", type=int, default=180)
    sp.add_argument("--no-hold", action="store_true", help="发现结束后立即关闭浏览器")

    sub.add_parser("probe", help="探测哪个页面能列出我的课程（排障用）")
    sub.add_parser("login", help="打开浏览器并等待你本人完成登录")
    sub.add_parser("courses", help="同步我的课程列表")

    sp = sub.add_parser("sync", help="同步课程章节目录与学习项")
    sp.add_argument("--course", default="", help="课程名或 id")
    sp.add_argument("--yes", action="store_true", help="跳过开始前的勾选确认")
    sp.add_argument("--headless", action="store_true", help="调试：不弹窗口（需已有登录 Cookie）")
    sp.add_argument("--keep", action="store_true", help="结束后保持浏览器窗口不关闭")
    sp.add_argument("--from-capture", default="", help="离线解析已抓取的章节页 HTML（不登录）")

    sp = sub.add_parser("study", help="自动学习未完成内容")
    sp.add_argument("--course", default="")
    sp.add_argument("--limit", type=int, default=0, help="本次最多处理多少项")
    sp.add_argument("--kinds", default="", help="只处理这些类型，如 video,document,ppt")
    sp.add_argument("--practice", action="store_true", help="遇到测验/练习时进入刷题辅助")
    sp.add_argument("--no-kb", action="store_true", help="学习时不构建知识库")
    sp.add_argument("--all", action="store_true", help="忽略完成状态，重做全部条目")
    sp.add_argument("--yes", action="store_true", help="跳过开始前的勾选确认")
    sp.add_argument("--headless", action="store_true", help="调试：不弹窗口（需已有登录 Cookie）")
    sp.add_argument("--keep", action="store_true", help="结束后保持浏览器窗口不关闭")

    sp = sub.add_parser("practice", help="刷题辅助（AI 分析 + 本地依据，提交由你完成）")
    sp.add_argument("--course", default="")
    sp.add_argument("--url", default="", help="练习/测验页面地址（留空则使用当前已打开页面）")
    sp.add_argument("--chapter", default="")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--auto-answer", dest="auto_answer", action="store_true",
                    help="练习/演示模式：把 AI 答案自动填入页面（需 user_config 的 PRACTICE_MODE=True 且非真实考试域名）")
    sp.add_argument("--auto-submit", dest="auto_submit", action="store_true",
                    help="练习/演示模式：结束后自动点击提交（额外需 PRACTICE_ALLOW_SUBMIT=True）")
    sp.add_argument("--yes", action="store_true", help="跳过开始前的勾选确认")
    sp.add_argument("--keep", action="store_true", help="结束后保持浏览器窗口不关闭")

    sp = sub.add_parser("kb", help="知识库：build/local/search/docs")
    sp.add_argument("action", choices=["build", "local", "search", "docs"])
    sp.add_argument("--course", default="")
    sp.add_argument("--dir", default="")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--top", type=int, default=6)
    sp.add_argument("query", nargs="*")
    sp.add_argument("--yes", action="store_true", help="跳过开始前的勾选确认")
    sp.add_argument("--headless", action="store_true", help="调试：不弹窗口（学习通会返回降级页，慎用）")
    sp.add_argument("--keep", action="store_true", help="结束后保持浏览器窗口不关闭")

    sp = sub.add_parser("search", help="统一搜索：资料 + 章节 + 题目 + 错题")
    sp.add_argument("query", nargs="+")
    sp.add_argument("--course", default="")
    sp.add_argument("--top", type=int, default=6)
    sp.add_argument("--ai", action="store_true", help="让 AI 综合本地资料给结论")

    sp = sub.add_parser("wrong", help="错题库：list/stats/export/drill/similar")
    sp.add_argument("action", choices=["list", "stats", "export", "drill", "similar"])
    sp.add_argument("--course", default="")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--fmt", default="md", choices=["md", "json", "csv"])
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--id", type=int, default=0)
    sp.add_argument("--n", type=int, default=2)

    sp = sub.add_parser("exam", help="开卷考试 AI 辅助（只读；需三重确认）")
    sp.add_argument("--course", default="")
    sp.add_argument("--url", default="")
    sp.add_argument("--gui", action="store_true", help="使用侧边栏窗口（默认按配置）")
    sp.add_argument("--console", action="store_true", help="强制终端模式")

    sub.add_parser("status", help="查看进度与统计")

    sp = sub.add_parser("grades", help="只读查看课程成绩与逐项明细(默认实时抓取并缓存)")
    sp.add_argument("course", nargs="?", default="", help="课程名称关键词")
    sp.add_argument("--cache", action="store_true", help="仅看本地缓存,不启动浏览器")
    sp.add_argument("--headless", action="store_true", help="调试：不弹窗口（需已有登录 Cookie）")

    sp = sub.add_parser("read", help="阅读/文档任务点：真实停留到估定时长（平台标注才算完成）")
    sp.add_argument("course", nargs="?", default="", help="课程名称关键词")
    sp.add_argument("--item", type=int, default=0, help="小节 id（默认取第一个未完成阅读项）")
    sp.add_argument("--min-seconds", dest="min_seconds", type=int, default=0, help="最低停留秒数（覆盖估算下限）")
    sp.add_argument("--headless", action="store_true", help="调试：不弹窗口（需已有登录 Cookie）")

    sp = sub.add_parser("discuss", help="计分讨论：默认预览AI草稿(只读)；--fill 填入当前编辑器(你点发表)；--submit 自动填入并提交(需开自动提交开关)；--dump 导出结构")
    sp.add_argument("course", nargs="?", default="", help="课程名称关键词")
    sp.add_argument("--dump", action="store_true", help="只读导出讨论区页面结构以适配选择器")
    sp.add_argument("--fill", action="store_true", help="把选定草稿填入当前打开的编辑器(需过三道门；绝不替你点发表/回复)")
    sp.add_argument("--kind", choices=("post", "reply", "all"), default="all",
                    help="模块选择：post=只发表(提问) / reply=只回复(约150字) / all=两类一起(默认)")
    sp.add_argument("--max", type=int, default=0, help="本次每轮最多处理条数(覆盖 DISCUSS_MAX 默认5,如 10/20)")
    sp.add_argument("--submit", action="store_true", help="全自动：逐条填入并自动点“发表/回复”(额外需 PRACTICE_ALLOW_DISCUSS_SUBMIT=True)")
    sp.add_argument("--pick", type=int, default=1, help="填入第几条草稿(默认 1)")
    sp.add_argument("--newpost", action="store_true", help="发帖前先点“新建话题”打开表单")
    sp.add_argument("--headless", action="store_true", help="调试：不弹窗口（需已有登录 Cookie）")

    sp = sub.add_parser("dump", help="导出页面 HTML，便于适配选择器")
    sp.add_argument("--url", default="")
    sp.add_argument("--tag", default="dump")

    sub.add_parser("selftest", help="离线自检（不联网：数据层/题库/检索/合规逻辑）")
    sub.add_parser("btest", help="离线页面结构自检（本地 fixture + Chromium）")
    return p


def main(argv: list[str] | None = None) -> int:
    init_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv == ["--gui"]:
        app = build_app()
        from ui.web_desktop import run_desktop

        try:
            return run_desktop(app.cfg)
        finally:
            app.store.close()
    if argv == ["--legacy-gui"]:
        app = build_app()
        from ui.workbench import run_workbench

        return run_workbench(app.cfg)
    args = _parser().parse_args(argv)
    try:
        app = build_app()
    except Exception as exc:
        get_logger("main").exception("初始化失败")
        err(f"初始化失败：{exc}")
        return 1
    cmd = args.cmd
    try:
        if getattr(args, "headless", False):
            app.headless = True
            warn("调试模式：不弹出浏览器窗口（仅使用已保存的登录态）")
        app.keep_open = bool(getattr(args, "keep", False))
        if cmd == "login":
            return cmd_login(app)
        if cmd == "gui":
            from ui.workbench import run_workbench

            return run_workbench(app.cfg)
        if cmd == "work":
            from ui.workbench import run_workbench

            return run_workbench(app.cfg)
        if cmd == "menu":
            return cmd_menu(app)
        if cmd == "setup":
            return cmd_setup(app, args.course)
        if cmd == "go":
            return cmd_go(app, args.course, limit=args.limit, kinds=args.kinds,
                          practice=args.practice, skip_study=args.no_study)
        if cmd == "ask":
            return cmd_ask(app, " ".join(args.text), course_key=args.course,
                           save=not args.no_save, top=args.top)
        if cmd == "exercises":
            return cmd_exercises(app, args.course, entry=args.entry,
                                 index=args.index, limit=args.limit)
        if cmd == "capture":
            return cmd_capture(app, args.course, wait=args.wait,
                               open_section=not args.no_section)
        if cmd == "pick":
            return cmd_pick(app, args.course, do_sync=not args.no_sync,
                            seconds=args.seconds, args_yes=args.yes, debug=args.debug)
        if cmd == "watch":
            return cmd_watch(app, args.seconds, hold=not args.no_hold)
        if cmd == "probe":
            return cmd_probe(app)
        if cmd == "courses":
            return cmd_courses(app)
        if cmd == "sync":
            return cmd_sync(app, args.course, yes=args.yes, args=args)
        if cmd == "study":
            return cmd_study(app, args)
        if cmd == "practice":
            return cmd_practice(app, args)
        if cmd == "kb":
            return cmd_kb(app, args)
        if cmd == "search":
            return cmd_search(app, args)
        if cmd == "wrong":
            return cmd_wrong(app, args)
        if cmd == "exam":
            if getattr(args, "console", False):
                args.gui = False
            else:
                args.gui = args.gui or (app.cfg.get("EXAM_UI") == "tkinter")
            return cmd_exam(app, args)
        if cmd == "grades":
            return cmd_grades(app, args)
        if cmd == "read":
            return cmd_read(app, args)
        if cmd == "discuss":
            return cmd_discuss(app, args)
        if cmd == "status":
            return cmd_status(app)
        if cmd == "dump":
            return cmd_dump(app)
        if cmd == "selftest":
            from tests.selftest import run_all

            return run_all()
        if cmd == "btest":
            from tests.browser_selftest import run_all as run_btest

            return run_btest()
    except KeyboardInterrupt:
        warn("已中断（Ctrl+C）。进度已保存在 SQLite 中，可重新运行继续。")
        return 130
    except Exception as exc:
        get_logger("main").exception("命令 %s 执行失败", cmd)
        err(f"{type(exc).__name__}: {exc}")
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
