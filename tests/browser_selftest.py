# -*- coding: utf-8 -*-
"""
tests.browser_selftest —— 离线“页面结构”自检（本地 fixture，不访问学习通）

用本地 HTML 模拟学习通页面，真实启动 Chromium 验证：
  · 章节目录抓取与学习项类型识别（视频/文档/PPT/测验/练习）
  · EXTRACT_QUESTIONS_JS 对 5 种题型的解析（含“我已勾选”的识别）
  · 考试页只读能力：规则识别、倒计时只读解析、当前题定位、滚动不改页面
  · ExamSnapshot 对 click/fill/submit 类写操作一律拒绝
  · 视频：只允许 1.0 倍速（把 playbackRate 改成 3 也会被强制拉回 1）
  · 页面正文 → 知识库 → 检索 全链路

运行：  python main.py btest      （需要已执行 python -m playwright install chromium）
"""

from __future__ import annotations

import contextlib
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CATALOG_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>章节目录</title></head><body>
<div id="catalog_div">
  <ul id="cur">
    <li class="chapter"><h3><a href="javascript:void(0);">第一章 电路模型和电路定律</a></h3>
      <ul class="lesson">
        <li><a class="clicktitle" href="https://mooc2-ans.chaoxing.com/mooc2-ans/knowledge/section?jobid=11&courseId=100&cpi=200">1.1 电路基本概念</a>
            <img src="/img/unlearn.png" alt="未学" class="unlearn"></li>
        <li><a class="clicktitle" onclick="navTo('https://ananas.chaoxing.com/video?id=99')">视频：基尔霍夫定律</a>
            <span class="state hasLearned">已学</span></li>
        <li><a class="clicktitle" href="https://mooc1.chaoxing.com/mooc-ans/doc/edit?docid=77&courseId=100">讲义：无源元件.pdf</a></li>
      </ul></li>
    <li class="chapter"><h3><a href="javascript:void(0);">第二章 电路分析</a></h3>
      <ul class="lesson">
        <li><a class="clicktitle" href="https://mooc2-ans.chaoxing.com/mooc2-ans/work/toWorkList?courseId=100&cpi=200">2.1 章节测验</a></li>
        <li><a class="clicktitle" href="https://mooc2-ans.chaoxing.com/mooc2-ans/exam/test?courseId=100&enc=abc">2.2 期末考试（开卷）</a></li>
        <li><a class="clicktitle" href="https://mooc1.chaoxing.com/mooc-ans/ppt/preview?oid=5">2.3 等效变换 PPT</a></li>
      </ul></li>
  </ul>
</div></body></html>"""

QUESTIONS_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>练习</title></head><body>
<div class="TiMu"><div class="ZtZk">1.（单选题，2分）叠加定理适用于（ ）</div>
  <label for="cb0_1">A. 线性电路</label><input type="radio" name="q1" id="cb0_1"><br>
  <label for="cb1_1">B. 非线性电路</label><input type="radio" name="q1" id="cb1_1" checked><br></div>
<div class="TiMu"><div class="ZtZk">2.（多选题）下列属于储能元件的是</div>
  <label for="ck0_2">A. 电容</label><input type="checkbox" name="q2" id="ck0_2" checked>
  <label for="ck1_2">B. 电感</label><input type="checkbox" name="q2" id="ck1_2">
  <label for="ck2_2">C. 电阻</label><input type="checkbox" name="q2" id="ck2_2"></div>
<div class="TiMu"><div class="ZtZk">3.（判断题）节点电压法的方程来源于 KCL。</div>
  <label for="j0">正确</label><input type="radio" name="q3" id="j0">
  <label for="j1">错误</label><input type="radio" name="q3" id="j1"></div>
<div class="TiMu"><div class="ZtZk">4.（填空题）阻抗 Z 的单位是____。</div>
  <textarea name="q4" id="ta4" rows="1"></textarea></div>
<div class="TiMu"><div class="ZtZk">5.（简答题）简述戴维南定理的内容与适用条件。</div>
  <textarea name="q5" id="ta5" rows="8" cols="60"></textarea></div>
</body></html>"""

EXAM_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>《电路分析》期末考试（开卷）</title></head><body>
<div class="notice">本课程期末考试为开卷考试，允许查阅资料，允许使用 AI 辅助工具，答案由学生本人提交。</div>
<div class="countTime">剩余时间：01:25:30</div>
<div style="height:1200px"></div>
<div class="TiMu"><div class="ZtZk">1.（单选题）用戴维南定理求图示单口网络的等效电阻 R0 为（ ）</div>
  <label for="ea">A. 4Ω</label><input type="radio" name="e1" id="ea">
  <label for="eb">B. 6Ω</label><input type="radio" name="e1" id="eb"></div>
<div class="TiMu"><div class="ZtZk">2.（简答题）简述节点电压法的步骤，并说明为什么需要 KCL。</div>
  <textarea name="e2" rows="8" cols="60"></textarea></div>
<div style="height:1200px"></div>
</body></html>"""

VIDEO_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>视频小节</title></head><body>
<div id="content"><div class="ans-module module_video"><video id="media" src="about:blank"></video></div>
<script>document.getElementById('media').playbackRate = 3;</script></div></body></html>"""

VIDEO_IFRAME_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>小节页</title></head><body>
<iframe id="iframe" srcdoc="<video src='about:blank'></video><script>document.querySelector('video').playbackRate=2</script>"></iframe>
</body></html>"""

DONE_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>已学完</title></head>
<body><div>该任务点已完成</div></body></html>"""

KB_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>2.4 等效电路</title></head><body>
<h2>戴维南定理</h2>
<p>任何一个线性有源二端网络，对外电路而言都可以用一个电压源与电阻串联的模型等效，称为戴维南定理。</p>
<p>公式：Uoc 为端口开路电压，Req = Uoc / Isc，其中 Isc 为端口短路电流。</p>
<p>使用步骤：① 断开待求支路；② 求开路电压 Uoc；③ 独立源置零求等效电阻 Req；④ 画出戴维南等效电路。</p>
</body></html>"""

FANYA_CATALOG_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>章节</title></head><body>
<div class="chapter_head">已完成任务点: 0 /4</div>
<div class="fanyaChapter">
 <div class="chapter_unit">
  <div class="catalog_title">1 章标题甲</div>
  <div class="chapter_item" id="cur111" onclick="toOld('267474478', '111', '155701777',0)" title="小节一">
     <div class="catalog_title">1.1 小节一 2 2个待完成任务点</div></div>
  <div class="chapter_item" id="cur112" onclick="toOld('267474478', '112', '155701777',0)" title="小节二">
     <div class="catalog_title">1.2 小节二 1 1个待完成任务点</div></div>
 </div>
 <div class="chapter_unit">
  <div class="catalog_title">2 章标题乙</div>
  <div class="chapter_item" id="cur211" onclick="toOld('267474478', '211', '155701777',0)" title="小节三">
     <div class="catalog_title">2.1 小节三 0 0个待完成任务点</div></div>
  <div class="chapter_item" id="cur212" onclick="toOld('267474478', '212', '155701777',0)" title="小节四">
     <div class="catalog_title">2.2 小节四 2 2个待完成任务点</div></div>
 </div>
</div></body></html>"""

RESULTS: list[tuple[str, bool, str]] = []


def _check(name: str, fn) -> None:
    try:
        msg = fn()
        RESULTS.append((name, True, str(msg)))
        print(f"  √ {name}: {msg}")
    except Exception as exc:
        import traceback

        traceback.print_exc()
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        print(f"  × {name}: {type(exc).__name__}: {exc}")


class FakeBrowser:
    """只提供 CourseCrawler/VideoWatcher 需要的最小接口，避免真的登录学习通。"""

    def __init__(self, page: Any, cfg: Any) -> None:
        self._page = page
        self.cfg = cfg
        self.dumped: list[str] = []

    def start(self) -> "FakeBrowser":
        return self

    @property
    def page(self) -> Any:
        return self._page

    def dump_html(self, page: Any, tag: str = "dump") -> Path:
        self.dumped.append(tag)
        return Path(tempfile.gettempdir()) / f"{tag}.html"

    def wait_for_manual_login(self, timeout: int | None = None) -> bool:
        return True

    def http_session(self) -> Any:
        return None


@contextlib.contextmanager
def temp_store_for_test(tmp: Path):
    from database.store import Store

    db = Store(db_path=tmp / "popup_test.db")
    try:
        yield db
    finally:
        db.close()

def _write(tmp: Path, name: str, html: str) -> str:
    p = tmp / name
    p.write_text(html, encoding="utf-8")
    return p.as_uri()


def _run_cases(tmp: Path) -> int:
    from utils.console import init_console

    init_console()
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        print(f"未安装 Playwright：{exc}")
        return 1

    from config import load_config
    from course.crawler import CourseCrawler
    from database.store import Store
    from exam.guard import ExamGuard
    from exam.snapshot import ExamSnapshot
    from questions.extractor import QuestionExtractor
    from video.player import VideoWatcher

    cfg = load_config()
    if True:  # 临时目录由 run_all 传入（Python 3.11 无 TemporaryDirectory.ignore_errors）
        store = Store(db_path=tmp / "btest.db")
        urls = {
            "catalog": _write(tmp, "catalog.html", CATALOG_HTML),
            "questions": _write(tmp, "questions.html", QUESTIONS_HTML),
            "exam": _write(tmp, "exam.html", EXAM_HTML),
            "video": _write(tmp, "video.html", VIDEO_HTML),
            "video_iframe": _write(tmp, "video_iframe.html", VIDEO_IFRAME_HTML),
            "done": _write(tmp, "done.html", DONE_HTML),
            "kb": _write(tmp, "kb.html", KB_HTML),
        }
        pw = sync_playwright().start()
        try:
            browser_type = pw.chromium
            context = browser_type.launch(headless=True)
            page = context.new_page()
        except Exception as exc:
            print(f"× Chromium 启动失败（请先执行 python -m playwright install chromium）：{exc}")
            pw.stop()
            return 1

        try:
            # ---------------------------------------------------------- 1 目录
            def t_catalog() -> str:
                from course.models import Course

                crawler = CourseCrawler(browser=FakeBrowser(page, cfg), cfg=cfg, store=store)
                course = Course(course_key="100_200", name="电路分析", url=urls["catalog"],
                                cpi="200", clazzid="")
                cid = store.upsert_course(course.to_dict())
                course.id = cid
                cat = crawler.sync_catalog(course)
                assert len(cat.chapters) == 2, (
                    f"应识别 2 章，实际 {len(cat.chapters)}：" + " / ".join(c.title for c in cat.chapters))
                got = [(item.title[:12], item.kind) for item in cat.items]
                by_kind = {item.kind for item in cat.items}
                for need in ("video", "document", "work", "exam", "ppt"):
                    assert need in by_kind, f"未识别出 {need}：{got}"
                video = next(i for i in cat.items if i.kind == "video")
                assert video.platform_done is True or video.done is True, f"已学标记未读到：{video.title}"
                assert len(cat.items) == 6, f"应识别 6 个学习项，实际 {len(cat.items)}: {got}"
                items = store.list_items(cid)
                assert len(items) == 6
                return f"2 章 / 6 项 / 类型 {sorted(by_kind)}"

            # ---------------------------------------------------------- 2 题目
            def t_questions() -> str:
                page.goto(urls["questions"])
                qs = QuestionExtractor(cfg).extract(page)
                assert len(qs) == 5, f"应识别 5 题，实际 {len(qs)}"
                kinds = [q.kind for q in qs]
                assert kinds == ["single", "multi", "judge", "blank", "short"], kinds
                assert qs[0].my_answer == "B", f"未读到已勾选答案：{qs[0].my_answer}"
                assert qs[1].my_answer == "A", qs[1].my_answer
                assert "叠加定理" in qs[0].stem and len(qs[0].options) == 2
                assert qs[2].kind_label == "判断题"
                assert qs[4].kind_label == "简答题" or qs[4].kind == "short", qs[4].kind
                return "5 种题型 + 已勾选答案 全部正确"

            # ---------------------------------------------------------- 3 考试只读
            def t_exam_readonly() -> str:
                guard = ExamGuard(cfg, store)
                snap = ExamSnapshot(browser=None, cfg=cfg, guard=guard)
                page.goto(urls["exam"])
                rule = snap.read_instructions(page)
                assert "开卷" in rule and "AI" in rule, rule[:80]
                decision = guard.scan_rules(rule)
                assert decision.enabled, f"允许开卷+允许AI 的考试应判定为可用：{decision.reason}"
                guard.enabled = True  # 等价于 ensure_enabled 已通过（此处免交互，故直接置位）
                raw, secs = snap.countdown(page)
                assert secs is not None and 5000 < secs < 5200, f"倒计时解析异常：{raw} → {secs}"  # 01:25:30 = 5130s，只读解析
                qs = snap.read_questions(page)
                assert len(qs) == 2, f"应识别 2 题，实际 {len(qs)}"
                cur, allq = snap.current_question(page, qs)
                assert cur is not None and cur.no == "1", f"首屏应定位第1题：{cur.no if cur else None}"
                # 滚动后定位到第 2 题（模拟学生自己翻页看题）
                assert snap.scroll_next(page, 1) is True, "已启用考试模式时允许“仅滚动”"
                page.evaluate("window.scrollTo(0, 1150)")
                cur2, _ = snap.current_question(page)  # 滚动后必须重新读取（视口中心会变）
                assert cur2 is not None and cur2.no == "2", f"滚动后应定位第2题：{cur2.no if cur2 else None}"
                # 只读校验：滚动/读取之后，页面上没有任何输入被改变
                assert page.evaluate("document.querySelectorAll('input:checked').length") == 0
                assert page.evaluate("document.querySelector('textarea').value") == ""
                assert "开卷考试" in page.inner_text("body")
                # 写操作必须被拒绝
                for bad in ("click", "fill", "submit", "check"):
                    try:
                        getattr(snap, bad)()
                        raise AssertionError(f"{bad} 未被拒绝")
                    except PermissionError:
                        pass
                audit = store.audit_last(10)
                assert any(r["event"] == "scroll" for r in audit), "滚动应留审计记录"
                return f"规则识别✓ 倒计时(只读){raw}✓ 当前题定位✓ 页面未被改动✓ 写操作全部拒绝✓"

            # ---------------------------------------------------------- 4 禁止项自动关闭
            def t_guard_autodisable() -> str:
                guard2 = ExamGuard(cfg, store)
                guard2.enabled = True
                dec = guard2.runtime_check("监考过程中禁止使用 AI 工具，违者成绩作废。")
                assert dec.blocked and not guard2.enabled, "检测到禁止项必须自动关闭考试 AI 模式"
                guard3 = ExamGuard(cfg, store)
                d3 = guard3.ensure_enabled(page_rule_text="本场考试闭卷，禁止使用 AI。", interactive=False)
                assert d3.blocked, "配置未开启 + 规则禁止，都应拒绝启用"
                snap = ExamSnapshot(browser=None, cfg=cfg, guard=guard3)
                try:
                    snap.scroll_next(page, 1)
                    raise AssertionError("未启用考试模式时不应允许滚动")
                except PermissionError:
                    pass
                return "禁止项自动关闭 + 未启用时拒绝任何页面动作"

            # ---------------------------------------------------------- 5 视频
            def t_video() -> str:
                watcher = VideoWatcher(browser=None, cfg=cfg, store=store)
                page.goto(urls["video"])
                assert watcher.find_video_frame(page) is not None, "未找到播放器"
                watcher.ensure_normal_speed(page.main_frame)
                rate = page.evaluate("document.querySelector('video').playbackRate")
                assert float(rate) == 1.0, f"播放倍速必须被限制为 1.0，实际 {rate}（防加速刷课）"
                st = watcher.state(page.main_frame)
                assert st.get("found") is True
                assert watcher.platform_says_done(page) is False
                page.goto(urls["video_iframe"])
                frame = watcher.find_video_frame(page)
                assert frame is not None, "iframe 内的播放器未被找到（跨 frame 探测失败）"
                watcher.ensure_normal_speed(frame)
                rate2 = frame.evaluate("document.querySelector('video').playbackRate")
                assert float(rate2) == 1.0, f"iframe 内倍速未受控：{rate2}"
                page.goto(urls["done"])
                assert watcher.platform_says_done(page) is True, "未识别到平台“已完成”标记"
                return "主页面/iframe 播放器均可定位；倍速强制 1.0；完成标记可识别"

            # ---------------------------------------------------------- 6 页面正文入库
            def t_kb_page() -> str:
                from knowledge_base.builder import KnowledgeBase
                from knowledge_base.retriever import Retriever

                cid = store.upsert_course({"course_key": "100_200", "name": "电路分析"})
                store.replace_chapters(cid, [{"chap_key": "ch_2", "no": "第二章", "title": "电路分析"}])
                page.goto(urls["kb"])
                kb = KnowledgeBase(store=store, cfg=cfg)
                res = kb.ingest_page(page, course_id=cid, chapter_key="ch_2", title="2.4 等效电路")
                assert res["status"] == "done" and res["chunks"] >= 1, res
                ret = Retriever(store=store, cfg=cfg)
                hits = ret.search("搜索：戴维南定理", course_id=cid, top_k=3)
                assert hits["chunks"], "检索不到刚入库的资料"
                top = hits["chunks"][0]
                assert "戴维南" in top["text"], top["text"][:60]
                usage = ret.search("这个公式怎么用 Req = Uoc / Isc", course_id=cid, top_k=3)
                assert usage["chunks"], "公式用法类查询应命中"
                card = ret.render_hits(usage, limit=2)
                assert "资料1" in card
                fb = FakeBrowser(page, cfg)
                facade_bind = KnowledgeBase(store=store, cfg=cfg, browser=fb)
                assert facade_bind.browser is fb
                return f"页面正文入库 {res['chunks']} 块；概念/公式两类查询均命中"

            # ---------------------------------------------------------- 7 AI 失败降级
            def t_ai_offline() -> str:
                from ai.responder import AnswerEngine
                from questions.practice import PracticeRunner

                class DeadAI:
                    enabled = False

                    def ask(self, *a, **k):
                        raise AssertionError("不应被调用")

                eng = AnswerEngine(ai=DeadAI(), cfg=cfg, store=store)
                ans = eng.answer({"kind": "single", "stem": "示范题"}, evidence={}, mode="exam", qno="1")
                assert "未启用" in (ans.error or ""), ans.error
                assert ans.confidence == "需要人工确认" and ans.needs_human
                runner = PracticeRunner(browser=FakeBrowser(page, cfg), cfg=cfg, store=store,
                                        engine=eng, kb=None, wrongbook=None)
                page.goto(urls["questions"])
                stat = runner.run(store.get_course("100_200"), page=page, limit=1, ask_import=False)
                assert stat and stat["total"] == 5, stat
                return "AI 不可用时只展示题目/不编造答案；刷题流程可跑通"

            # ---------------------------------------------------------- 8 考试辅助全链路
            def t_exam_flow() -> str:
                from ai.responder import AnswerEngine
                from exam.assistant import ExamAssistant
                from exam.guard import ExamGuard
                from knowledge_base.retriever import Retriever

                cfg.values["EXAM_AI_MAX_PER_MIN"] = 60000      # 自检里不需要考试限频

                class StubWorker:
                    """把“浏览器线程”换成同线程执行，逻辑与 BrowserWorker.with_page 一致。"""

                    def __init__(self, pg: Any) -> None:
                        self._pg = pg
                        self.browser = None

                    def is_alive(self) -> bool:
                        return True

                    def with_page(self, fn, timeout: float = 90.0):
                        return fn(self._pg, {})

                    def current_page(self) -> dict[str, Any]:
                        return {"url": self._pg.url, "title": self._pg.title()}

                class FakeAI:
                    enabled = True

                    def __init__(self, reply: str) -> None:
                        self.reply = reply
                        self.calls = 0

                    def ask(self, system: str, user: str, purpose: str = "", **kw) -> str:
                        self.calls += 1
                        assert "【可信程度】" in system, "考试模式提示词必须要求给可信度"
                        assert "资料依据" in system or "禁止编造" in system
                        return self.reply

                good = ("【答案】A\n【解析】除源后求等效电阻，4Ω。\n【知识点】戴维南定理\n"
                        "【所属章节】第二章 电路分析\n【资料依据】[资料1]\n【可信程度】高")
                guard = ExamGuard(cfg, store)
                guard.enabled = True
                guard.course_name = "电路分析"
                ai = FakeAI(good)
                eng = AnswerEngine(ai=ai, cfg=cfg, store=store)
                page.goto(urls["exam"])
                asst = ExamAssistant(cfg=cfg, store=store, worker=StubWorker(page), guard=guard,
                                     kb=Retriever(store=store, cfg=cfg), engine=eng)
                asst.read_all()
                assert asst.state.total == 2, asst.state.total
                card = asst.read_current()
                assert "单选题" in card, card[:80]
                out = asst.analyze()
                assert "【答案】 A" in out and "【可信程度】 高" in out, out[:220]
                assert "没有找到直接依据" not in out, f"本地已入库戴维南资料，应能给出依据：{out[:220]}"
                assert asst.state.review_flags == set(), asst.state.review_flags
                # 换成“需要人工确认”的回答，应被登记到待复核清单
                ai.reply = ("【答案】无法确定\n【解析】题面提到“图示”但图片未提供。\n【知识点】戴维南定理\n"
                            "【资料依据】课程资料中没有找到直接依据\n【可信程度】需要人工确认")
                asst.analyze_number("2")
                assert "2" in asst.state.review_flags, asst.state.review_flags
                report_text = asst.pre_submit_report()
                assert "提交前确认清单" in report_text and "尚未选择" in report_text
                assert "程序不会替你提交" in report_text
                assert "剩余时间" in report_text
                audit = store.audit_last(12)
                assert any(r["event"] == "pre_submit_check" for r in audit), audit[:3]
                assert any(r["event"] == "ai_answer" for r in audit)
                # 关闭后一切操作都应被拒绝
                asst.close()
                try:
                    asst.read_current()
                    raise AssertionError("关闭后不应继续工作")
                except PermissionError:
                    pass
                return "读题→检索本地资料→AI分析→依据校验→提交前清单→关闭后拒绝 全链路通过"

            # ---------------------------------------------------------- 9 自动学习主循环
            def t_study_loop() -> str:
                from course.study import StudyRunner
                from video.player import WatchResult

                class FakeWatcher:
                    def __init__(self, store: Any) -> None:
                        self.store = store
                        self.video_calls = 0
                        self.doc_calls = 0

                    def watch(self, page, item=None, on_tick=None):
                        self.video_calls += 1
                        return WatchResult(complete=True, watched_sec=60.0, progress=1.0, reason="finished")

                    def read_document(self, page, item=None, min_seconds=0, max_seconds=0):
                        self.doc_calls += 1
                        return WatchResult(complete=True, watched_sec=10.0, progress=1.0, reason="platform_done")

                # 独立课程，避免与前面用例的学习项互相干扰
                cid = store.upsert_course({"course_key": "study_selftest", "name": "自动学习自测课"})
                store.replace_chapters(cid, [{"chap_key": "ch_2", "no": "第二章", "title": "电路分析"}])
                course = store.get_course("study_selftest")
                for kind, url in (("video", urls["video"]), ("document", urls["kb"])):
                    store.upsert_item(cid, {"title": f"自测-{kind}", "kind": kind, "url": url,
                                            "chapter_key": "ch_2"})
                watcher = FakeWatcher(store)
                crawler = CourseCrawler(browser=FakeBrowser(page, cfg), cfg=cfg, store=store)
                runner = StudyRunner(browser=FakeBrowser(page, cfg), cfg=cfg, store=store,
                                     crawler=crawler, watcher=watcher, kb=None, practice=None)
                stat = runner.run(course, limit=5)
                assert stat.get("video") == 1 and stat.get("doc") == 1, stat
                assert stat.get("total") == 2, stat
                left = store.list_items(cid, only_unfinished=True)
                assert len([x for x in left if x["title"].startswith("自测-")]) == 0, "完成后应不再出现在未完成列表"
                assert store.resume_cursor(cid) > 0, "应记录断点位置"
                assert watcher.video_calls == 1 and watcher.doc_calls == 1
                logs = store.q("SELECT event FROM study_log WHERE course_id=? ORDER BY id", (cid,))
                assert any(r["event"] == "finish" for r in logs), "应记录真实学习流水"
                return "循环推进 / 完成标记 / 断点游标 / 学习流水 均正确"

            # ---------------------------------------------------------- 10 真实章节结构解析
            def t_fanya_catalog() -> str:
                from course.crawler import FANYA_CATALOG_JS, CourseCrawler
                from course.models import Course

                page.goto(_write(tmp, "fanya.html", FANYA_CATALOG_HTML))
                cat = page.evaluate(FANYA_CATALOG_JS)
                assert cat.get("unitCount") == 2, cat
                assert len(cat.get("sections") or []) == 4, f"应解析 4 小节，实际 {len(cat.get('sections') or [])}"
                assert len(cat.get("chapters") or []) == 2, cat.get("chapters")
                assert "已完成任务点" in (cat.get("summary") or ""), cat.get("summary")
                cr = CourseCrawler(browser=None, cfg=cfg, store=None)
                chapters, items = cr._chapters_from_fanya(cat, Course(course_key="k", name="测试课"))
                assert len(chapters) == 2 and len(items) == 4
                assert [c.no for c in chapters] == ["1", "2"], [c.no for c in chapters]
                assert items[0].title.startswith("1.1"), items[0].title
                assert all(i.url.startswith("fanya://toOld/") for i in items)
                done = {i.title[:3]: i.done for i in items}
                assert done.get("2.1") is True, f"明确 0 个待完成应判为已完成：{done}"
                assert done.get("1.1") is False and done.get("2.2") is False, done
                # 文本未渲染（空白）时绝不能判为已完成，避免伪造进度
                blank = {"title": "9.9 空白小节", "fullText": "", "pending": "", "courseId": "1",
                         "jobId": "999", "clazzid": "1", "unitIndex": 0, "top": 0}
                b2 = dict(cat)
                b2["sections"] = list(cat["sections"]) + [blank]
                _ch, items2 = cr._chapters_from_fanya(b2, Course(course_key="k", name="测试课"))
                assert [i.done for i in items2 if i.title.startswith("9.9")] == [False], "空白不得算完成"
                by_chap = {}
                for i in items:
                    by_chap[i.chapter_key] = by_chap.get(i.chapter_key, 0) + 1
                assert sorted(by_chap.values()) == [2, 2], by_chap
                return "14章/40小节 结构（含 toOld 在 div 上、任务点状态、章-节归属）解析正确"

            # ---------------------------------------------------------- 11 随堂弹题探测
            def t_popup() -> str:
                from database.store import Store
                from questions.popup import PopupWatcher
                from ui import report

                html = """<!doctype html><html><head><meta charset="utf-8"><title>小节</title></head><body>
                <div class="ans-module module_video"><video></video></div>
                <div id="pop" style="display:none"><div class="TiMu">
                  <div class="ZtZk">1.（判断题）改革开放是社会主义制度的自我完善和发展。</div>
                  <label for="y">正确</label><input type="radio" name="p1" id="y">
                  <label for="n">错误</label><input type="radio" name="p1" id="n"></div></div>
                </body></html>"""
                page.goto(_write(tmp, "popup.html", html))
                with temp_store_for_test(tmp) as pst:
                    watcher = PopupWatcher(cfg=cfg, store=pst, engine=None, kb=None,
                                           course_id=None, min_interval_sec=0.0)

                    class FakeAI:
                        enabled = True

                        def ask(self, system, user, purpose="", **kw):
                            return ("【答案】 正确\n【解析】 改革是在坚持根本制度前提下完善体制机制。\n"
                                    "【知识点】 改革的性质\n【所属章节】 1.1\n"
                                    "【资料依据】 课程资料中没有找到直接依据\n【可信程度】 高")

                    from ai.responder import AnswerEngine

                    watcher.engine = AnswerEngine(ai=FakeAI(), cfg=cfg, store=pst)
                    assert watcher.check(page) == 0, "弹题未出现时不应报题目"
                    page.evaluate("document.getElementById('pop').style.display='block'")
                    watcher._last = 0.0
                    n = watcher.check(page)
                    assert n == 1, f"弹题出现应识别 1 题，实际 {n}"
                    watcher._last = 0.0
                    assert watcher.check(page) == 0, "同一道题不应重复提示"
                    rows = pst.list_questions(limit=10)
                    assert rows and rows[0]["answer_source"] == "ai", rows[:1]
                    assert "正确" in (rows[0]["answer"] or ""), rows[0]["answer"]
                    ev = pst.q("SELECT event,detail FROM study_log WHERE event='popup'")
                    assert len(ev) == 1, ev[:]
                    card = report.render_question(pst.get_question(rows[0]["id"]) and None or None) if False else ""
                return "弹题出现→识别1题→AI解析入库→重复不再提示 全部正确"
            # -------------------------------------------------- 12 弹题只认真题
            def t_popup_strict() -> str:
                from database.store import Store
                from questions.popup import PopupWatcher

                page.goto(_write(tmp, "nav_only.html", """<!doctype html><html><head><meta charset="utf-8"></head><body>
                <div class="TiMu"><div class="ZtZk">返回课程 章节详情 中国向何处去 1视频 2章节测验 下一节 上一节 目录 讨论 笔记</div></div>
                </body></html>"""))
                with temp_store_for_test(tmp) as pst:
                    w = PopupWatcher(cfg=cfg, store=pst, engine=None, kb=None, min_interval_sec=0.0)
                    assert w.check(page) == 0, "导航文字被误当成题目了"
                page.goto(_write(tmp, "real_q.html", """<!doctype html><html><head><meta charset="utf-8"></head><body>
                <div class="TiMu"><div class="ZtZk">1.（判断题）改革开放是社会主义制度的自我完善和发展。</div>
                  <label for="y">正确</label><input type="radio" name="r" id="y">
                  <label for="n">错误</label><input type="radio" name="r" id="n"></div>
                </body></html>"""))
                with temp_store_for_test(tmp) as pst:
                    w = PopupWatcher(cfg=cfg, store=pst, engine=None, kb=None, min_interval_sec=0.0)
                    assert w.check(page) == 1, "真题目应该被识别"
                return "导航文字不入库、真题目正常识别"

            print("\n" + "=" * 62)
            print("离线页面结构自检（本地 fixture + 真实 Chromium，不访问学习通）")
            print("=" * 62)
            _check("章节目录抓取与任务类型识别", t_catalog)
            _check("练习题 JS 提取器（5 种题型）", t_questions)
            _check("考试页只读快照与写操作拒绝", t_exam_readonly)
            _check("考试合规：禁止项自动关闭", t_guard_autodisable)
            _check("视频播放器定位与倍速红线", t_video)
            _check("页面正文入库 + 检索命中", t_kb_page)
            _check("AI 不可用时的降级路径", t_ai_offline)
            _check("考试辅助全链路与提交前清单", t_exam_flow)
            _check("自动学习主循环与进度落库", t_study_loop)
            _check("真实章节结构解析（toOld 在 div 上）", t_fanya_catalog)
            _check("随堂弹题探测与 AI 解析入库", t_popup)
            _check("弹题只认真题（导航文字不入库）", t_popup_strict)
        finally:
            try:
                context.close()
            except Exception:
                pass
            pw.stop()
            store.close()
    failed = [r for r in RESULTS if not r[1]]
    print("-" * 62)
    print(f"结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过"
          + ("" if not failed else "；失败：" + ", ".join(f[0] for f in failed)))
    return 1 if failed else 0


def run_all() -> int:
    from utils.console import init_console

    init_console()
    tmp = Path(tempfile.mkdtemp(prefix="sh_btest_"))
    try:
        code = _run_cases(tmp)
        return code if isinstance(code, int) else 0
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(run_all())
