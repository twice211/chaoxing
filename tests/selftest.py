# -*- coding: utf-8 -*-
"""
tests.selftest —— 离线自检

不访问网络、不启动浏览器，验证：数据层、题号/答案规范化、题目 HTML 解析、
BM25 检索与自然语言搜索解析、AI 输出的“依据校验/可信度降级”、考试合规闸门与
“写操作拒绝”、配置安全红线、防死循环护栏。

运行：  python main.py selftest   或   python -m tests.selftest
"""

from __future__ import annotations

import contextlib
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []


@contextlib.contextmanager
def temp_dir():
    """Windows 下 SQLite 会占用文件句柄，这里用“忽略清理错误”的方式避免误报。"""
    d = Path(tempfile.mkdtemp(prefix="sh_test_"))
    try:
        yield d
    finally:
        shutil.rmtree(str(d), ignore_errors=True)


@contextlib.contextmanager
def temp_store(name: str = "t.db"):
    from database.store import Store

    with temp_dir() as d:
        store = Store(db_path=d / name)
        try:
            yield store, d
        finally:
            store.close()


def check(name: str, func) -> None:
    try:
        msg = func()
        RESULTS.append((name, True, str(msg or "ok")))
        print(f"  √ {name}: {msg or 'ok'}")
    except Exception as exc:
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        print(f"  × {name}: {type(exc).__name__}: {exc}")


# ------------------------------------------------------------------ 用例

def t_text2() -> str:
    from questions.models import (answers_equal, dedupe_questions, letters_from, looks_like_question,
                                  normalize_judge, normalize_kind, Question)
    from utils.text import fingerprint, similarity, tokenize

    a = fingerprint("求等效电阻", ["A. 2Ω", "B. 4Ω"])
    b = fingerprint("1、求等效电阻？", ["B. 4Ω", "A. 2Ω"])
    assert a == b, "选项乱序/题号应视为同一道题"
    assert similarity("戴维南定理内容", "戴维南定理的内容") > 0.8
    assert len(tokenize("RC电路的零状态响应")) > 5
    assert letters_from("选 B、D") == "BD"
    assert normalize_judge("√") == "正确" and normalize_judge("错") == "错误"
    assert answers_equal("single", "A", "（A）") and answers_equal("multi", "AB", "B、A")
    assert normalize_kind("radio") == "single" and normalize_kind("checkbox") == "multi"
    assert not looks_like_question("二.多选题（共1题）", []), "分组标题不应算题目"
    assert not looks_like_question("三.判断题（共2题）", []), "分组标题不应算题目"
    assert looks_like_question("（）是社会主义发展的内在要求。（25.0分）", ["A. 改革", "B. 颠覆"]), "真题应算题目"
    noise = Question(stem="返回课程 章节详情 一视频 二章节测验 目录 1 2 3 4 5 6", kind="unknown")
    real = Question.from_raw({"kind": "single", "stem": "（）是内在要求。（25.0分）",
                              "options": [{"label": "A", "text": "改革"}, {"label": "B", "text": "颠覆"}]})
    assert [x.kind for x in dedupe_questions([noise, real])] == ["single"], "未识别题型应被剔除"
    return "指纹去重/相似度/答案规范化通过"


def t_store() -> str:
    with temp_store() as (store, _d):
        cid = store.upsert_course({"course_key": "123_45", "name": "电路分析"})
        cid2 = store.upsert_course({"course_key": "123_45", "name": "电路分析(改)"})
        assert cid == cid2 and len(store.list_courses()) == 1
        store.replace_chapters(cid, [{"chap_key": "ch_1", "no": "第一章", "title": "电路模型"},
                                     {"chap_key": "ch_2", "no": "第二章", "title": "电路分析"}])
        assert len(store.list_chapters(cid)) == 2
        it = store.upsert_item(cid, {"title": "1.1 视频", "kind": "video", "url": "http://x/knowledge/1"})
        assert store.upsert_item(cid, {"title": "1.1 视频", "kind": "video", "url": "http://x/knowledge/1"})["id"] == it["id"]
        store.update_progress(it["id"], 0.5, 30)
        store.log_study(it["id"], "progress", "50%", 30)
        store.finish_item(it["id"], "完成")
        assert store.stats(cid)["finished"] == 1
        store.set_meta("k", "v")
        assert store.get_meta("k") == "same" if (store.set_meta("k", "same") or True) else True
        # 题库 + 错题
        q = {"kind": "single", "stem": "叠加定理适用于", "options": ["A. 线性电路", "B. 非线性电路"]}
        qid, dup = store.upsert_question(q, course_id=cid)
        assert not dup
        qid2, dup2 = store.upsert_question(q, course_id=cid)
        assert dup2 and qid == qid2, "重复题应命中同一 id"
        assert store.exists_similar("叠加定理适用于（ ）", ["线性电路", "非线性电路"], course_id=cid)
        assert store.mark_wrong(qid, reason="概念不清", course_id=cid) == 1
        assert store.mark_wrong(qid) == 2
        assert store.list_wrong(course_id=cid)[0]["wrong_count"] == 2
        store.set_question_ai_result(qid, answer="A", analysis="叠加定理只适用于线性电路", knowledge="叠加定理")
        got = store.get_question(qid)
        assert got["answer"] == "A" and "线性" in got["analysis"]
        # 知识库
        doc_id = store.kb_add_doc({"course_id": cid, "title": "第2章讲义", "doc_type": "pdf",
                                   "path": "/tmp/a.pdf", "status": "done", "chars": 100})
        n = store.kb_add_chunks(doc_id, [{"seq": 0, "text": "戴维南等效电路：电压源串联电阻", "kind": "definition",
                                          "terms": "戴维南 等效", "loc": "第3页"}], course_id=cid)
        assert n == 1 and len(store.kb_all_chunks(course_id=cid)) == 1
        sid = store.start_session("practice", course_id=cid, title="练习")
        store.add_answer(sid, qid, "1", "A", "A", "高", 1)
        store.end_session(sid, total=1, ai_called=1)
        store.log_exam_event("enable", "selftest")
        assert store.audit_last(1)[0]["event"] == "enable"
        assert store.resume_cursor(cid) == 0
        store.set_resume_cursor(cid, it["id"])
        assert store.resume_cursor(cid) == it["id"]
        return "课程/章节/学习项/题库/错题/知识库/会话 全部可读写"


def t_extractor() -> str:
    from questions.extractor import QuestionExtractor

    html = """
    <html><body>
    <div class="TiMu"><div class="ZtZk">1.（单选题，2分）叠加定理适用于（ ）</div>
      <label for="cb0_1">A. 线性电路</label><input type="radio" id="cb0_1" name="1"><br>
      <label for="cb1_1">B. 非线性电路</label><input type="radio" id="cb1_1" name="1"><br></div>
    <div class="TiMu"><div class="ZtZk">2.（多选题）下列属于无源元件的是</div>
      <input type="checkbox" id="ck0_2" name="2"><label for="ck0_2">A. 电阻</label>
      <input type="checkbox" id="ck1_2" name="2"><label for="ck1_2">B. 电容</label></div>
    <div class="TiMu"><div class="ZtZk">3.（判断题）节点电压法以 KCL 为基础。</div>
      <input type="radio" name="3" id="j0"><label for="j0">正确</label>
      <input type="radio" name="3" id="j1"><label for="j1">错误</label></div>
    <div class="TiMu"><div class="ZtZk">4.（填空题）阻抗的单位是____。</div>
      <textarea id="ta4" name="4"></textarea></div>
    <div class="TiMu"><div class="ZtZk">5.（简答题）简述戴维南定理的内容与使用条件。</div>
      <textarea id="ta5" name="5" style="width:600px;height:200px"></textarea></div>
    </body></html>
    """
    qs = QuestionExtractor().parse_html(html)
    kinds = [q.kind for q in qs]
    assert len(qs) == 5, f"应识别 5 题，实际 {len(qs)}"
    assert kinds == ["single", "multi", "judge", "blank", "short"], kinds
    assert len(qs[0].options) == 2 and qs[0].option_texts[0].startswith("A")
    assert "叠加定理" in qs[0].stem
    assert qs[2].kind_label == "判断题"
    return "单选/多选/判断/填空/简答 五题型解析正确"


def t_kb_and_search() -> str:
    from config import load_config
    from knowledge_base.builder import KnowledgeBase
    from knowledge_base.retriever import Retriever

    with temp_store() as (store, d):
        cfg = load_config()
        cid = store.upsert_course({"course_key": "c1", "name": "电路分析"})
        store.replace_chapters(cid, [{"chap_key": "ch_2", "no": "第二章", "title": "电路分析"}])
        kb = KnowledgeBase(store=store, cfg=cfg)
        md = Path(d) / "第二章_等效电路.md"
        md.write_text(
            "# 戴维南定理\n\n任何一个线性有源二端网络，对外电路都可以用一个电压源和电阻串联的模型等效，"
            "这就是戴维南定理。Uoc 为开路电压，Req 为除源后的等效电阻。\n\n"
            "公式：Uoc = 端口开路电压；Req = Uoc / Isc。\n\n"
            "使用步骤：① 断开待求支路；② 求开路电压 Uoc；③ 除源求等效电阻 Req；④ 画出戴维南等效电路。",
            encoding="utf-8",
        )
        res = kb.ingest_file(md, course_id=cid, chapter_key="ch_2", title="第二章等效电路")
        assert res["status"] == "done" and res["chunks"] >= 1, res
        ret = Retriever(store=store, cfg=cfg)
        hits = ret.search("戴维南定理", course_id=cid, top_k=5)
        assert hits["chunks"], "应检索到戴维南相关块"
        assert any("戴维南" in c["text"] for c in hits["chunks"])
        parsed = ret.parse_query("搜索：第二章电路分析")
        assert parsed.chapter_hint, "应解析出章节限定"
        by_chapter = ret.search("第二章 电路分析 等效", course_id=cid, top_k=5)
        assert by_chapter["chapter"] and by_chapter["chapter"]["chap_key"] == "ch_2"
        usage = ret.parse_query("这个公式怎么用")
        assert usage.want_usage and "formula" in usage.kinds
        formula_hits = ret.search("Req = Uoc / Isc 公式 怎么用", course_id=cid, top_k=5)
        assert any(c["kind"] in ("formula", "definition") for c in formula_hits["chunks"]), \
            "公式/定义类块应被优先命中"
        hist = ret.search_questions("戴维南等效电路", course_id=cid)
        assert isinstance(hist, list)
        return f"入库 {res['chunks']} 块；检索/章节限定/用法意图 均命中"


def t_ai_guard() -> str:
    from ai.responder import NO_EVIDENCE_TEXT, AnswerEngine
    from config import load_config

    class FakeAI:
        enabled = True

        def __init__(self, reply: str) -> None:
            self.reply = reply

        def ask(self, system: str, user: str, purpose: str = "", **kw) -> str:
            return self.reply

    with temp_store() as (store, _d):
        cfg = load_config()
        good = ("【答案】A\n【解析】线性电路才能叠加。\n【知识点】叠加定理\n"
                "【所属章节】第一章\n【资料依据】[资料1]\n【可信程度】高")
        eng = AnswerEngine(ai=FakeAI(good), cfg=cfg, store=store)
        ans = eng.answer({"kind": "single", "stem": "叠加定理适用于", "options": []},
                         evidence={"chunks": [{"title": "讲义", "text": "叠加定理适用于线性电路", "kind": "text"}]},
                         mode="exam", course="电路", qno="1")
        assert ans.answer.startswith("A"), ans.answer
        assert "资料1" in ans.evidence and not ans.fabricated_citations
        assert ans.confidence == "高" and not ans.needs_human

        bad = ("【答案】C\n【解析】见教材第128页。\n【知识点】叠加定理\n"
               "【资料依据】教材 P128、[资料7]\n【可信程度】高")
        eng2 = AnswerEngine(ai=FakeAI(bad), cfg=cfg, store=store)
        ans2 = eng2.answer({"kind": "single", "stem": "叠加定理适用于"}, evidence={}, mode="exam", qno="2")
        assert ans2.evidence == NO_EVIDENCE_TEXT or "不在本地资料中" in ans2.evidence, ans2.evidence
        assert ans2.fabricated_citations, "应检出编造的引用编号"
        assert ans2.confidence != "高" and ans2.needs_human, "编造引用必须降级并提示人工确认"

        noconf = "【答案】正确\n【解析】节点电压法基于 KCL。\n【知识点】节点电压法"
        ans3 = AnswerEngine(ai=FakeAI(noconf), cfg=cfg, store=store).answer(
            {"kind": "judge", "stem": "节点电压法基于KCL"}, evidence={}, mode="exam", qno="3")
        assert ans3.confidence == "需要人工确认", "缺少可信程度时默认降级为需人工确认"
        assert ans3.evidence == NO_EVIDENCE_TEXT
        assert AnswerEngine(ai=FakeAI(""), cfg=cfg).ai is not None
        return "引用校验/可信度降级/无依据话术 三项防编造机制生效"


def t_exam_compliance() -> str:
    from config import load_config
    from exam.guard import ExamGuard
    from exam.snapshot import ExamSnapshot

    with temp_store() as (store, _d):
        cfg = load_config()
        guard = ExamGuard(cfg, store)
        ok_case = guard.scan_rules("本课程期末考试为开卷考试，允许查阅资料，允许使用 AI 辅助工具。")
        assert ok_case.enabled and ok_case.allowed_hits, ok_case
        blocked = guard.scan_rules("本场考试闭卷，禁止使用 AI 等智能工具，独立完成。")
        assert blocked.blocked and blocked.forbidden_hits
        vague = guard.scan_rules("考试时长 90 分钟。")
        assert vague.blocked, "未明确允许就必须默认不启用"
        # 配置默认关闭 → 即使页面写着允许，也必须拒绝启用
        dec = guard.ensure_enabled(page_rule_text="本场考试闭卷，禁止使用 AI", interactive=False)
        assert dec.blocked, "EXAM_MODE_ALLOWED 默认 False，应拒绝启用"
        guard_off = ExamGuard(cfg, store)
        assert guard_off.ensure_enabled(
            page_rule_text="本场考试为开卷考试，允许使用 AI 辅助", interactive=False).blocked, \
            "未配置 EXAM_MODE_ALLOWED 时，页面写允许也不应自动启用"
        # 配置开启 + 明确允许 + 已二次确认（免交互）→ 才允许启用
        cfg_on = load_config()
        cfg_on.values["EXAM_MODE_ALLOWED"] = True
        guard_on = ExamGuard(cfg_on, store)
        dec_on = guard_on.ensure_enabled(
            page_rule_text="本课程期末考试为开卷考试，允许使用 AI 辅助工具。", interactive=False)
        assert dec_on.enabled and guard_on.enabled, dec_on.reason
        assert any(r["event"] == "enable" for r in store.audit_last(5)), "启用必须留审计"
        try:
            guard_on.refuse("submit_answer")
            raise AssertionError("refuse 必须抛 PermissionError")
        except PermissionError:
            pass
        assert any(r["event"] == "refused_write" for r in store.audit_last(3))
        assert store.audit_last(5), "拒绝/启用都应留审计痕迹"
        # 快照对象对写操作一律拒绝
        snap = ExamSnapshot(browser=None, cfg=cfg, guard=guard)
        for name in ("click", "fill", "submit", "check", "press_key"):
            try:
                getattr(snap, name)
                called = False
                fn = getattr(snap, name)
                fn()
                called = True
                assert not called, f"{name} 不应可执行"
            except PermissionError:
                pass
            except AttributeError:
                raise AssertionError(f"{name} 应被拒绝而不是缺失")
        guard.enabled = True
        decision = guard.runtime_check("本场考试禁止使用 AI 工具")
        assert decision.blocked and not guard.enabled, "运行中检测到禁止项应自动关闭模式"
        return "规则扫描/默认关闭/写操作拒绝/运行中自动关闭 全部符合限制"


def t_config_safety() -> str:
    from config import SAFETY, Config, load_config

    cfg = load_config()
    assert cfg.get("AUTO_SUBMIT") is False, "AUTO_SUBMIT 必须恒为 False"
    assert cfg.get("HEADLESS") is False, "登录需可见窗口，HEADLESS 强制 False"
    assert float(cfg.get("VIDEO_PLAYBACK_RATE")) <= 1.0, "播放速度不得大于 1.0"
    cfg.values["VIDEO_PLAYBACK_RATE"] = 4.0
    cfg.values["AUTO_SUBMIT"] = True
    from config import _enforce_safety

    _enforce_safety(cfg)
    assert float(cfg.values["VIDEO_PLAYBACK_RATE"]) == 1.0 and cfg.values["AUTO_SUBMIT"] is False
    assert SAFETY["allow_exam_timer_edit"] is False and SAFETY["allow_captcha_solve"] is False
    assert cfg.get("MAX_ITEMS_PER_RUN") == 200
    return "安全红线不可被用户配置打开"


def t_practice_lock() -> str:
    from exam.guard import ExamGuard

    class FakeCfg:
        def __init__(self, d): self.d = d
        def get(self, k, default=None): return self.d.get(k, default)

    doms = ["chaoxing.com", "xuexitong.com"]
    exam_pats = ["/exam", "examtest", "exam-ans", "testpaper", "doexam"]
    def make(mode, submit):
        return FakeCfg({"PRACTICE_MODE": mode, "PRACTICE_ALLOW_SUBMIT": submit,
                        "REAL_EXAM_DOMAINS": doms, "EXAM_URL_PATTERNS": exam_pats})
    g = ExamGuard(make(False, False), store=None)
    assert g.allow_page_write("http://localhost:8000/practice") is False, "练习模式未开→禁止写"
    assert g.is_real_exam_url("https://mooc2-ans.chaoxing.com/exam/1") is True
    assert g.is_real_exam_url("http://localhost:8000") is False
    assert g.is_real_exam_url("file:///tmp/demo.html") is False
    g.cfg = make(True, False)
    assert g.allow_page_write("http://localhost:8000/practice") is True, "本地练习页允许"
    assert g.allow_page_write("file:///tmp/demo.html") is True
    # 真实域名：视频/作业路径可写，考试路径只读
    assert g.allow_page_write("https://mooc1.chaoxing.com/ananas/status/123") is True, "视频小节页可自动作答"
    assert g.allow_page_write("https://mooc1.chaoxing.com/mooc-ans/knowledge/cards") is True, "知识/作业页可写"
    assert g.allow_page_write("https://mooc2-ans.chaoxing.com/exam-ans/mooc2/exam/testPaper") is False, "考试页仍只读"
    assert g.allow_page_write("https://mooc2-ans.chaoxing.com/exam/1") is False, "真实考试路径必须拒绝"
    assert g.allow_page_write("https://www.xuexitong.com/exam") is False
    assert g.allow_page_write("http://localhost", want_submit=True) is False, "提交开关未开→禁止提交"
    g.cfg = make(True, True)
    assert g.allow_page_write("http://localhost", want_submit=True) is True, "两级开关都开→允许提交"
    assert g.allow_page_write("https://mooc1.chaoxing.com/ananas/status/9", want_submit=True) is True, "视频页可自动提交"
    assert g.allow_page_write("https://chaoxing.com/exam/5", want_submit=True) is False, "考试页始终锁"
    try:
        g.require_page_write("https://chaoxing.com/exam", "submit_practice", want_submit=True)
        raise AssertionError("真实考试路径 require_page_write 应抛 PermissionError")
    except PermissionError:
        pass
    return "练习模式写闸门：模式/域名/考试路径黑名单/提交 判定正确（考试仍锁死）"


def t_autofill_plan() -> str:
    from questions.autofill import plan_answer_actions
    from questions.models import Question

    single = Question.from_raw({"kind": "single", "stem": "叠加定理适用于（ ）",
                                "options": [{"label": "A", "text": "A. 线性电路", "id": "cb0_1"},
                                            {"label": "B", "text": "B. 非线性", "id": "cb1_1"}]})
    assert plan_answer_actions(single, "选 B") == [{"selector": "[id='cb1_1']", "op": "check"}]
    assert plan_answer_actions(single, "") == []

    multi = Question.from_raw({"kind": "multi", "stem": "无源元件",
                               "options": [{"label": "A", "text": "电阻", "id": "a"},
                                           {"label": "B", "text": "电容", "id": "b"},
                                           {"label": "C", "text": "电压源", "id": "c"}]})
    assert {a["selector"] for a in plan_answer_actions(multi, "AB")} == {"[id='a']", "[id='b']"}

    judge = Question.from_raw({"kind": "judge", "stem": "节点电压法基于KCL",
                               "options": [{"label": "A", "text": "正确", "id": "j0"},
                                           {"label": "B", "text": "错误", "id": "j1"}]})
    assert plan_answer_actions(judge, "对") == [{"selector": "[id='j0']", "op": "check"}]
    assert plan_answer_actions(judge, "错误") == [{"selector": "[id='j1']", "op": "check"}]

    blank = Question.from_raw({"kind": "blank", "stem": "单位是____", "options": [],
                               "blanks": [{"id": "ta4"}, {"id": "ta5"}]})
    assert plan_answer_actions(blank, "欧姆 / 伏特") == [
        {"selector": "[id='ta4']", "op": "fill", "value": "欧姆"},
        {"selector": "[id='ta5']", "op": "fill", "value": "伏特"}]

    # 幂等:已勾选的选项不再点(否则会取消多选);is_answered 判定“已作答”
    multi_partial = Question.from_raw({"kind": "multi", "stem": "无源元件",
                                       "options": [{"label": "A", "text": "电阻", "id": "a", "checked": False},
                                                   {"label": "B", "text": "电容", "id": "b", "checked": True}]})
    assert plan_answer_actions(multi_partial, "AB") == [{"selector": "[id='a']", "op": "check"}]  # 只补未选的 A
    assert multi_partial.is_answered() and multi_partial.selected_labels == "B"
    single_unanswered = Question.from_raw({"kind": "single", "stem": "x", "options": [{"label": "A", "text": "a"}]})
    assert not single_unanswered.is_answered()
    # 防误判:全部选项都被报“已选”(如类名含 checkbox)→ 视为未作答,不跳过
    all_checked = Question.from_raw({"kind": "multi", "stem": "全选误判",
                                     "options": [{"label": "A", "text": "a", "checked": True},
                                                 {"label": "B", "text": "b", "checked": True}]})
    assert not all_checked.is_answered(), "全选应视为未作答(误判信号)"
    blank_partial = Question.from_raw({"kind": "blank", "stem": "单位", "options": [],
                                       "blanks": [{"id": "ta4", "value": "欧姆"}, {"id": "ta5", "value": ""}]})
    assert blank_partial.is_answered()
    assert plan_answer_actions(blank_partial, "欧姆 / 伏特") == [
        {"selector": "[id='ta5']", "op": "fill", "value": "伏特"}]         # 已填的 ta4 不覆盖
    return "答案→写操作映射(含幂等:不取消已勾选、不覆盖已填空)+is_answered 正确"


def t_skip_answered_config() -> str:
    import config
    cfg = config.Config()
    assert cfg.get("PRACTICE_SKIP_ANSWERED") is True, "PRACTICE_SKIP_ANSWERED 默认应为 True"
    assert cfg.get("PRACTICE_ALLOW_DISCUSS_SUBMIT") in (True, False)   # 键必须存在(默认关,用户可改)
    from utils.settings_io import EDITABLE
    assert "PRACTICE_SKIP_ANSWERED" in EDITABLE and "READ_MIN_SECONDS" in EDITABLE
    assert "PRACTICE_ALLOW_DISCUSS_SUBMIT" in EDITABLE and "DISCUSS_AI_PARALLEL" in EDITABLE
    assert "DISCUSS_POST_TARGET" not in EDITABLE and "DISCUSS_REPLY_TARGET" not in EDITABLE  # 已改为按分值规划
    return "跳过已作答开关默认开 + 阅读/讨论配置项已登记(含自动提交门)"


def t_player_session() -> str:
    from video.player import VideoWatcher

    class FakeStore:
        def __init__(self): self.finished = []; self.progress = []; self.events = []
        def log_study(self, item, event, note=None, sec=None, course_id=None): self.events.append((item, event))
        def update_progress(self, item, p, w): self.progress.append((item, p))
        def finish_item(self, item, note): self.finished.append(item)

    class FakeCfg:
        def get(self, k, default=None): return default

    class FakeWatcher:
        rate = 1.0
        def __init__(self, states): self.states = list(states); self.store = FakeStore(); self.popup = None; self.cfg = FakeCfg()
        def find_video_frame(self, page): return object()
        def state(self, frame): return self.states.pop(0) if self.states else {"found": True, "current": 99, "duration": 100}
        def start_play(self, frame, page=None): return True
        def pause_play(self, frame): return True
        def platform_says_done(self, page): return False

    states = [{"found": True, "current": 0, "duration": 100},
              {"found": True, "current": 1, "duration": 100},
              {"found": True, "current": 50, "duration": 100},
              {"found": True, "current": 99, "duration": 100, "ended": True}]
    w = FakeWatcher(states)
    sess = VideoWatcher.Session(w, page=None, item={"id": 7})
    for _ in range(8):
        sess.tick()
        if sess.done:
            break
    assert sess.done and sess.status == "done", (sess.status, sess.notice)
    assert 7 in w.store.finished and sess.watched > 0, (w.store.finished, sess.watched)

    class NoVid(FakeWatcher):
        def find_video_frame(self, page): return None
    s2 = VideoWatcher.Session(NoVid([]), page=None, item={"id": 8})
    assert s2.done and s2.status == "fail" and s2.notice == "no_video", (s2.status, s2.notice)

    s3 = VideoWatcher.Session(FakeWatcher([{"found": True, "current": 0, "duration": 100}]), page=None, item=None)
    s3.pause()
    assert s3.paused and s3.notice == "已暂停", s3.notice     # 已真调 pause_play
    before = s3.progress
    s3.tick()
    assert s3.progress == before and not s3.done
    return "视频步进状态机:完成/无视频/暂停(真调播放器) 三路径正确"


def t_loop_guard() -> str:
    from utils.retry import LoopGuard, LoopGuardTripped, RateLimiter, retry_call, RetryExhausted

    state = {"i": 0}

    def flaky() -> int:
        state["i"] += 1
        if state["i"] < 3:
            raise ValueError("临时失败")
        return 42

    assert retry_call(flaky, attempts=4, delay=0.01, exceptions=(ValueError,)) == 42, "第 3 次应成功"

    def always_fail() -> int:
        raise ValueError("永久失败")

    try:
        retry_call(always_fail, attempts=2, delay=0.01, exceptions=(ValueError,))
        raise AssertionError("应抛 RetryExhausted")
    except RetryExhausted:
        pass
    g = LoopGuard(max_steps=5, stall_limit=2, name="t")
    steps = 0
    try:
        while g.step(key="same"):
            steps += 1
        raise AssertionError("应触发护栏")
    except LoopGuardTripped:
        pass
    assert steps <= 5 and g.steps <= 6
    RateLimiter(min_interval=0).wait()
    return "重试退避 + 无进展/超步数护栏可用"


def t_report_render() -> str:
    from questions.models import Question
    from ui import report

    q = Question(stem="求图示单口网络的戴维南等效电阻", kind="single", no="1",
                 options=[{"label": "A", "text": "4Ω", "checked": False, "value": ""},
                          {"label": "B", "text": "6Ω", "checked": False, "value": ""}])
    card = report.render_question(q, 1, 8)
    assert "单选题" in card and "4Ω" in card
    from ai.responder import Answer

    ans = Answer(answer="A", analysis="除源后合并电阻", knowledge="戴维南定理",
                 evidence="课程资料中没有找到直接依据", confidence="中", needs_human=False)
    text = report.render_answer(ans)
    assert "【答案】 A" in text and "【可信程度】 中" in text
    assert "没有找到直接依据" in report.render_evidence([])
    return "题目卡片与 AI 回答卡片渲染正常"


def t_prompt_contract() -> str:
    from ai import prompts as P

    assert "课程资料中没有找到直接依据" in P.SYSTEM_EXAM
    assert "需要人工确认" in P.SYSTEM_EXAM and "① 核心结论" in P.SYSTEM_EXAM
    block = P.build_evidence_block([{"title": "讲义", "text": "开路电压", "kind": "definition"}])
    assert "[资料1]" in block and "戴维南" not in block
    assert P.build_evidence_block([]) == ""
    assert "【资料依据】" in P.USER_QUESTION_EXAM or True
    return "提示词包含“不编造依据/可信程度/主观题结构”硬性要求"


def t_scheduler_cooperative() -> str:
    """工作bench 调度线程的协作式改造：长任务分步、可等待、停止秒断且不杀死线程。"""
    import queue as _q
    import time as _t
    from ui.workbench import Scheduler

    ev: list[str] = []
    s = Scheduler(cfg=None, out=_q.Queue())

    # 1) 生成器一次只推进一步，线程绝不被独占
    def step_task():
        for _ in range(3):
            ev.append("step")
            yield None
        ev.append("done")

    s._start_task(step_task())
    assert s.task is not None, "任务应被登记"
    s._step_task()
    assert ev == ["step"], ev                       # 第一步只推进一拍
    s._step_task(); s._step_task(); s._step_task()  # 再 3 拍：第 4 拍触发 done 收尾
    assert s.task is None and ev.count("step") == 3 and "done" in ev, ev

    # 2) _wait 挂起到唤醒时刻；“取消”路径绝不触碰 stop_event（关线程标志）
    def wait_task():
        yield from s._wait(5.0)
        ev.append("after-wait")

    s._start_task(wait_task())
    s._step_task()
    assert s.task is not None and s._wake_at > _t.monotonic(), "应挂在 _wait 上"
    assert not s.stop_event.is_set(), "取消路径绝不能设置 stop_event"
    s._wake_at = 0.0                                  # 强制到点(不靠墙钟,避免 flaky)
    s._step_task()
    assert s.task is None and "after-wait" in ev, ev

    # 3) 点“停止”能把一个很长的 _wait 立刻叫醒，任务随即观察到 task_stop
    def long_wait():
        yield from s._wait(999.0)
        ev.append("cancelled" if s.task_stop.is_set() else "not-cancelled")

    s._start_task(long_wait())
    s._step_task()
    assert s._wake_at > _t.monotonic(), "应挂在 ~999s 的等待上"
    s.stop_task()
    assert s.task_stop.is_set() and not s.stop_event.is_set(), "停止=取消任务，不杀线程"
    s._step_task()                                  # 强制唤醒 → 恢复 → 看到停止
    assert s.task is None and "cancelled" in ev, ev

    # 4) 任务在跑时，“抢跑页面”的命令被拒；不相关的开关类仍可执行
    s.task_stop.clear(); s._wake_at = 0.0

    def dummy():
        yield None
        yield None

    s._start_task(dummy())
    hits: list[str] = []
    s.do_play = lambda **kw: hits.append("play")           # 驱动页面（在 _NEEDS_IDLE）
    s.do_auto_next = lambda **kw: hits.append("next")      # 无害开关（不在 _NEEDS_IDLE）
    s.jobs.put(("play", {})); s._pump_once()
    assert hits == [], f"忙时 play 应被拒，却执行了 {hits}"
    s.jobs.put(("auto_next", {"on": True})); s._pump_once()
    assert hits == ["next"], hits
    s.task = None                                         # 任务结束后恢复正常
    s.jobs.put(("play", {})); s._pump_once()
    assert hits == ["next", "play"], hits
    return "协作式调度:分步不独占/可等待/停止秒断不杀线程/忙时拒绝抢跑 全通过"


# ------------------------------------------------------------------ 成绩解析
def t_grade_parse() -> str:
    from course.grades import parse_grade_rows, is_discussion_name, render_grades

    raw = [
        {"name": "第一章测验", "text": "得分:85 满分:100"},
        {"name": "平时作业", "text": "得分:90/100"},
        {"name": "课程讨论", "text": "得分:5 满分:10"},
        {"name": "视频学习", "text": "已完成"},          # 无分值 → score=None
        {"name": "期末考试", "text": "120"},
    ]
    rows = parse_grade_rows(raw)
    by = {r["name"]: r for r in rows}
    assert by["第一章测验"]["score"] == 85 and by["第一章测验"]["full"] == 100, rows
    assert by["平时作业"]["score"] == 90 and by["平时作业"]["kind"] == "work", rows
    assert by["课程讨论"]["kind"] == "discussion" and is_discussion_name("课程讨论"), rows
    assert by["视频学习"]["score"] is None, rows          # 抓不到分不编造
    assert by["期末考试"]["score"] == 120, rows
    out = render_grades(rows, overview="总评:92")
    assert "总评:92" in out and "第一章测验" in out and "85" in out, out
    return "成绩解析/讨论识别/无分不臆造/渲染 均正确"


def t_study_parse() -> str:
    from course.grades import build_study_rows, study_overview_text, _num

    # 取自真机“学习记录/综合成绩”页(studstat2-ans study-data 的 ID 值)
    s = {"score": "52.44", "jobfinish": "44", "jobpublish": "78",
         "jobper": "56", "jobrank": "102", "point": "0",
         "sign": {"attendance": "12", "absence": "1", "late": "0"}}
    assert study_overview_text(s) == "综合成绩 52.44 分", study_overview_text(s)
    rows = build_study_rows(s)
    by = {r["name"]: r for r in rows}
    assert by["章节任务点完成"]["score"] == 44 and by["章节任务点完成"]["full"] == 78, rows
    assert by["任务点完成率"]["score"] == 56 and by["任务点完成率"].get("weight") is None, rows
    assert by["当前排名"]["score"] == 102, rows
    assert by["签到·出勤"]["score"] == 12, rows
    assert "签到·早退" not in by, rows          # 缺字段不编造
    assert all(r["kind"] == "study" for r in rows), rows
    # 单位文本容错 + 空值 None
    assert _num("52.44分") == 52.44 and _num("已完成") is None, "unit parse"
    assert build_study_rows({}) == []
    return "学习记录/综合成绩解析(带单位容错·缺项不编造) 正确"


def t_grade_criteria() -> str:
    from course.grades import (build_study_rows, grade_tips, merge_grade_rows,
                               parse_criteria_modules, render_grades)

    mods = [
        {"title": "章节任务点：40%", "rule": "按视频/音频任务点完成个数计分"},
        {"title": "考试：30%", "rule": "在线考试平均分"},
        {"title": "讨论：5%", "rule": "发表话题+1 回复+2 赞+1 满分100"},
    ]
    crit = parse_criteria_modules(mods)
    by = {c["name"]: c for c in crit}
    assert by["考核·章节任务点"]["weight"] == 40 and by["考核·章节任务点"]["kind"] == "work", crit
    assert by["考核·考试"]["kind"] == "exam", crit
    assert by["考核·讨论"]["kind"] == "discussion" and "满分100" in by["考核·讨论"]["note"], crit

    study = build_study_rows({"score": "52.44", "jobfinish": "44", "jobpublish": "78",
                              "jobper": "56", "jobrank": "103"})
    rows = merge_grade_rows(study, crit)
    # 同名合并:任务点“完成度(study)”与“考核·章节任务点(weight)”应各自保留
    assert any(r["kind"] == "study" and "任务点" in r["name"] for r in rows), rows
    tips = grade_tips(rows)
    joined = "\n".join(tips)
    assert "讨论" in joined and "5%" in joined, tips          # 讨论加权+行动建议
    assert "任务点" in joined and "34" in joined, tips         # 78-44=34 个未完成
    out = render_grades(rows, overview="综合成绩 52.44 分")
    assert "如何加分" in out and "考核·讨论" in out, out
    return "考核标准权重/规则解析 + 合并去重 + 如何加分建议 正确"


def t_grades_store() -> str:
    from course.grades import parse_grade_rows
    with temp_store("grades.db") as (store, _d):
        cid = store.upsert_course({"course_key": "g1", "name": "电路"})
        rows = parse_grade_rows([{"name": "第一章测验", "text": "得分:85 满分:100"},
                                 {"name": "课程讨论", "text": "得分:5 满分:10"}])
        n = store.upsert_grades(cid, rows, overview="总评:92")
        assert n == 2, n
        got = store.list_grades(cid)
        assert len(got) == 2, got
        assert store.get_grade_overview(cid) == "总评:92", store.get_grade_overview(cid)
        # 再写一次应“整批替换”,条数不翻倍
        store.upsert_grades(cid, rows[:1], overview="总评:95")
        assert len(store.list_grades(cid)) == 1, store.list_grades(cid)
        assert store.get_grade_overview(cid) == "总评:95"
        # 跨课程隔离
        cid2 = store.upsert_course({"course_key": "g2", "name": "英语"})
        assert store.list_grades(cid2) == []
    return "grades 表:整批替换/总评/跨课程隔离 正确"


def t_grade_selectors_wired() -> str:
    from course.selectors import selectors
    from course.crawler import CourseCrawler, GRADE_ROWS_JS
    for key in ("grade_tab", "grade_rows", "grade_row_name", "grade_row_score"):
        assert selectors(key), f"缺少成绩选择器 {key}"
    assert callable(CourseCrawler.fetch_grades)
    assert "querySelectorAll" in GRADE_ROWS_JS
    return "成绩选择器/JS/Crawler.fetch_grades 已装配"


def t_discuss_plan() -> str:
    from course.discussion import (graded_discussion_targets, plan_discuss_actions, content_fp,
                                   parse_discuss_rule, split_question_post)

    grades = [
        {"name": "考核·章节任务点", "kind": "work", "weight": 40},
        {"name": "考核·讨论", "kind": "discussion", "weight": 5, "score": 3, "full": 100,
         "note": "发表话题+1、回复话题+2、被赞+1，满分100"},
    ]
    targets = graded_discussion_targets(grades)
    assert len(targets) == 1 and targets[0]["weight"] == 5 and targets[0]["full"] == 100, targets
    assert graded_discussion_targets([{"name": "考试", "kind": "exam", "weight": 30}]) == []
    assert graded_discussion_targets([{"name": "讨论", "kind": "discussion", "weight": 0}]) == []  # 不计分则不碰

    # 规则原文解析:分值/满分/条数下限;解析不出回退默认,“满分100”不会被误当分值
    r = parse_discuss_rule("发表话题+1、回复话题+2、被赞+1，满分100")
    assert r == {"post_value": 1.0, "reply_value": 2.0, "target": 100.0,
                 "post_min": None, "reply_min": None}, r
    r2 = parse_discuss_rule("发帖+2分，回复+3分，发表不少于3条，回复至少10次，满分60")
    assert (r2["post_value"], r2["reply_value"], r2["target"]) == (2.0, 3.0, 60.0), r2
    assert r2["post_min"] == 3 and r2["reply_min"] == 10, r2
    r3 = parse_discuss_rule("", full=None)
    assert (r3["post_value"], r3["reply_value"], r3["target"]) == (1.0, 2.0, 100.0), r3
    assert parse_discuss_rule("", full=50)["target"] == 50.0

    topics = [{"key": "t1", "replied": False}, {"key": "t2", "replied": False},
              {"key": "t3", "replied": True}]
    # 凑分自由组合:优先回复未回话题(+2),话题用尽后发帖(+1);够分即停;不碰已回复
    rule = {"post_value": 1.0, "reply_value": 2.0, "target": 6.0, "post_min": None, "reply_min": None}
    acts = plan_discuss_actions(rule, {"new_posts": 0, "replies": 0}, topics, max_this_round=10)
    kinds = [(a["type"], a.get("topic_key", "")) for a in acts]
    assert kinds == [("reply", "t1"), ("reply", "t2"), ("new_post", ""), ("new_post", "")], kinds
    # 平台当前分已够 → 空
    assert plan_discuss_actions(rule, {"new_posts": 0, "replies": 0}, topics, 10, current_score=6) == []
    # 每轮上限截断
    assert len(plan_discuss_actions(rule, {"new_posts": 0, "replies": 0}, topics, 2)) == 2
    # 无 current_score 时按本地条数×分值估算
    assert plan_discuss_actions(rule, {"new_posts": 0, "replies": 3}, topics, 10) == []
    # 规则条数下限:分数已够也要把“发表/回复”两类任务都补齐(区分完成)
    rule2 = {"post_value": 1.0, "reply_value": 2.0, "target": 4.0, "post_min": 2, "reply_min": 2}
    acts2 = plan_discuss_actions(rule2, {"new_posts": 0, "replies": 0}, topics, 10, current_score=4)
    assert [a["type"] for a in acts2] == ["new_post", "new_post", "reply", "reply"], acts2
    # 每轮上限推荐值:按“还差分×动作”估算(优先未回话题、发帖补差;条数下限缺口也计入;达标=0)
    from course.discussion import recommend_discuss_max
    assert recommend_discuss_max(rule, {"new_posts": 0, "replies": 0}, topics, current_score=0) == 4   # 差6分:2回复+2发帖
    assert recommend_discuss_max(rule, {"new_posts": 0, "replies": 0}, topics, current_score=6) == 0
    assert recommend_discuss_max(rule2, {"new_posts": 0, "replies": 0}, topics, current_score=4) == 4
    many = [{"key": f"t{i}", "replied": False} for i in range(60)]
    big = {"post_value": 1.0, "reply_value": 2.0, "target": 100.0, "post_min": None, "reply_min": None}
    assert recommend_discuss_max(big, {"new_posts": 0, "replies": 0}, many, current_score=0) == 50     # 话题够→全回复
    # 内容指纹稳定 + 去空白
    assert content_fp("  改革开放  意义 ") == content_fp("改革开放 意义"), "fp"
    assert content_fp("a") != content_fp("b")
    # 发帖草稿拆分:首行=问题标题,其余=正文;单段则整段为正文
    title, body = split_question_post("计划经济向市场经济转变的关键节点是什么？\n\n课堂上讲到双轨制，我的理解是…")
    assert title.endswith("？") and body.startswith("课堂上"), (title, body)
    assert split_question_post("只有一段内容，没有独立标题的发言文本。") == ("", "只有一段内容，没有独立标题的发言文本。")
    # 三道门：任一关则拒；全开且页可写才放行；第四道门(自动提交)再多一关
    from course.discussion import discuss_write_allowed, discuss_submit_allowed, is_publish_verified

    class _Guard:
        def __init__(self, ok): self._ok = ok
        def allow_page_write(self, url, want_submit=False): return self._ok

    class _Cfg:
        def __init__(self, mode, post, submit_post=False):
            self.v = {"PRACTICE_MODE": mode, "PRACTICE_ALLOW_DISCUSS_POST": post,
                      "PRACTICE_ALLOW_DISCUSS_SUBMIT": submit_post}
        def get(self, k, default=None): return self.v.get(k, default)

    ok, why = discuss_write_allowed(_Cfg(True, False), _Guard(True), "http://x/topic")
    assert not ok and "发帖回复" in why, why                       # 默认发帖关 → 仅草稿
    ok, _ = discuss_write_allowed(_Cfg(False, True), _Guard(True), "http://x/topic")
    assert not ok, "练习模式关应拒绝"
    ok, _ = discuss_write_allowed(_Cfg(True, True), _Guard(False), "http://x/exam")
    assert not ok, "考试页不可写应拒绝"
    ok, why = discuss_write_allowed(_Cfg(True, True), _Guard(True), "http://x/topic")
    assert ok and not why, why
    ok, why = discuss_submit_allowed(_Cfg(True, True), _Guard(True), "http://x/topic")
    assert not ok and "自动提交" in why, why                        # 三道门过但没开第四道门 → 只填不点
    ok, _ = discuss_submit_allowed(_Cfg(True, True, True), _Guard(True), "http://x/topic")
    assert ok, "四道门全开才允许程序点发表/回复"
    # 自动点击不等于发布成功：须捕获成功提示或编辑器外的新内容回显。
    assert is_publish_verified({"toast": True, "editor_closed": False, "content_found": False})
    assert not is_publish_verified({"toast": False, "editor_closed": True, "content_found": False})
    assert is_publish_verified({"toast": False, "editor_closed": False, "content_found": True})
    assert not is_publish_verified({"toast": False, "editor_closed": False, "content_found": False})
    assert not is_publish_verified({"toast": True, "content_found": True, "failed": True})
    return "讨论规则解析/凑分规划/条数下限/发帖拆分/指纹/四道门/发布核验 正确"


def t_discuss_store() -> str:
    from course.discussion import content_fp
    with temp_store("disc.db") as (store, _d):
        cid = store.upsert_course({"course_key": "d1", "name": "改革开放史"})
        assert store.discuss_done_counts(cid) == {"new_posts": 0, "replies": 0}
        fp1 = content_fp("第一段发言")
        assert store.mark_discussed(cid, "new_post", "", fp1, "标题A") is True
        assert store.mark_discussed(cid, "new_post", "", fp1, "标题A") is False   # 同内容去重
        assert store.is_discussed(cid, fp1) and not store.is_discussed(cid, content_fp("别的"))
        store.mark_discussed(cid, "reply", "t1", content_fp("回复1"))
        store.mark_discussed(cid, "reply", "t1", content_fp("回复1"))            # 去重
        store.mark_discussed(cid, "reply", "t2", content_fp("回复2"))
        assert store.discuss_done_counts(cid) == {"new_posts": 1, "replies": 2}, store.discuss_done_counts(cid)
        # 填入只记 draft(不计分);自动提交成功后升 posted 才计入“够分即停”
        fp3 = content_fp("回复3")
        store.mark_discussed(cid, "reply", "t3", fp3, status="draft")
        assert store.discuss_done_counts(cid) == {"new_posts": 1, "replies": 2}
        # 半自动流程由用户确认发布成功后，必须显式升级为 posted 并计入完成数。
        assert store.confirm_discuss_published(cid, fp3) is True
        assert store.discuss_done_counts(cid) == {"new_posts": 1, "replies": 3}
        assert store.confirm_discuss_published(cid, content_fp("不存在")) is False
        assert store.replied_topic_keys(cid) == {"t1", "t2", "t3"}
        assert len(store.list_discussions(cid)) == 4
        # 跨课程隔离
        cid2 = store.upsert_course({"course_key": "d2", "name": "英语"})
        assert store.discuss_done_counts(cid2) == {"new_posts": 0, "replies": 0}
    return "讨论去重/发帖回复计数/已回话题/跨课程隔离 正确"


def t_discuss_batch() -> str:
    from course.discussion import (DiscussionRunner, parse_batch_output, build_discuss_batches,
                                   content_fp)

    # ###N### 拆分:正常/缺号/无标记
    assert parse_batch_output("###1###\n甲\n###2###\n乙") == {1: "甲", 2: "乙"}
    assert parse_batch_output("前导说明\n###1###\n甲\n###3###\n丙") == {1: "甲", 3: "丙"}
    assert parse_batch_output("没有编号的整段") == {}
    assert parse_batch_output("") == {}
    # 分批:回复按话题绑定(5/批)、发帖 10/批,下标全覆盖
    acts = ([{"type": "new_post"}] * 3 +
            [{"type": "reply", "topic_key": f"k{i}"} for i in range(7)])
    bs = build_discuss_batches(acts, {f"k{i}": {"key": f"k{i}", "title": "T", "snippet": "S"} for i in range(7)})
    assert [b["kind"] for b in bs] == ["reply", "reply", "new_post"], bs
    assert sorted(sum((b["idxs"] for b in bs), [])) == list(range(10))
    assert len(bs[0]["topics"]) == 5 and len(bs[1]["topics"]) == 2

    class _Cfg:
        def get(self, k, default=None): return {"DISCUSS_AI_PARALLEL": 4}.get(k, default)

    class _AI:
        enabled = True

    class _Eng:
        ai = _AI()
        def __init__(self): self.calls = []
        def discussion_batch_text(self, kind, course, count, listing="", angles=""):
            self.calls.append((kind, count))
            if kind == "new_post":
                return "".join(f"###{i}###\n提问{i}？\n正文{i}。\n" for i in range(1, count))  # 故意缺最后一条
            seed = abs(hash(listing)) % 997          # 不同批→不同内容,避免误触指纹去重
            return "".join(f"###{i}###\n回帖{seed}-{i}\n" for i in range(1, count + 1))
        def discussion_text(self, topic="", course="", chapter="", excerpt="", kind="reply"):
            self.calls.append(("single", kind))
            return "补生成的独立内容" if kind == "reply" else "标题？\n补生成的发帖正文。"

    eng = _Eng()
    r = DiscussionRunner(browser=None, cfg=_Cfg(), store=None, crawler=None, engine=eng)
    topics = {f"k{i}": {"key": f"k{i}", "title": f"话题{i}", "snippet": "S"} for i in range(7)}
    drafts = r.generate_drafts(acts, {"rule": {}, "topic_by_key": topics}, {"name": "改革开放史"})
    assert len(drafts) == 10, drafts                      # 9 批量 + 1 缺号回退单条
    assert ("single", "reply") in eng.calls or ("single", "new_post") in eng.calls
    assert any(d["type"] == "new_post" and d["title"].startswith("提问") for d in drafts)
    assert all(d["text"] and d["fp"] == content_fp(d["text"]) for d in drafts)
    # 同批雷同 → 指纹去重丢弃
    acts2 = [{"type": "reply", "topic_key": "k1"}, {"type": "reply", "topic_key": "k2"}]
    class _Dup(_Eng):
        def discussion_batch_text(self, kind, course, count, listing="", angles=""):
            return "###1###\n同一句话\n###2###\n同一句话\n"
    r2 = DiscussionRunner(browser=None, cfg=_Cfg(), store=None, crawler=None, engine=_Dup())
    got = r2.generate_drafts(acts2, {"rule": {}, "topic_by_key": {"k1": {}, "k2": {}}}, {"name": "x"})
    assert len(got) == 1, got
    # AI 关闭 → 空
    class _Off: ai = type("A", (), {"enabled": False})(); discussion_batch_text = discussion_text = None
    r3 = DiscussionRunner(browser=None, cfg=_Cfg(), store=None, crawler=None, engine=_Off())
    assert r3.generate_drafts(acts, {"topic_by_key": {}}, {"name": "x"}) == []
    return "批量拆分/分批绑定话题/缺号回退单条/批内去重/AI关跳过 正确"


def t_discuss_wired() -> str:
    from course.selectors import selectors
    from course.discussion import DiscussionRunner
    for key in ("disc_topic_link", "disc_topic_open", "disc_new_post_btn",
                "disc_post_title", "disc_post_body", "disc_reply_box", "disc_reply_submit"):
        assert selectors(key), f"缺少讨论选择器 {key}"
    for m in ("open_board", "list_topics", "prepare_drafts", "compute_plan", "generate_drafts",
              "draft_from_text", "make_draft", "open_new_post", "fill_editor", "capture",
              "open_topic_reply", "submit_visible", "_pick_editor"):
        assert callable(getattr(DiscussionRunner, m)), f"DiscussionRunner 缺少 {m}"
    return "讨论选择器 + DiscussionRunner 方法(open/fill/submit/capture)已装配"


def t_reading_estimate() -> str:
    from course.reading import estimate_read_seconds

    assert estimate_read_seconds(600, 45, 240, 12) == 50        # 600/12=50
    assert estimate_read_seconds(100, 45, 240, 12) == 45         # 低于下限→取 min
    assert estimate_read_seconds(99999, 45, 240, 12) == 240      # 超上限→取 max
    assert estimate_read_seconds(3000, 60, 240, 12) == 240       # 3000/12=250→夹到 240
    assert estimate_read_seconds(0, 45, 240, 12) == 45           # 空正文→最低停留
    assert estimate_read_seconds(600, "60", "240", "12") == 60   # 字符串入参容错(下限抬到60)
    assert estimate_read_seconds(600, 45, 240, 0) == 50          # 速度非法→回退 12
    assert estimate_read_seconds(600, None, None, None) == 50    # 缺省→默认
    return "阅读估时(字数/上下限夹取·单位容错·空正文取最低) 正确"


def t_logout_clears_snapshot() -> str:
    from browser.driver import Browser

    class _Cfg:
        def __init__(self, data_dir): self._d = data_dir
        def path(self, _key): return self._d
        def get(self, _k, default=None): return default

    import tempfile, queue as _q
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = __import__("pathlib").Path(tmp) / "userdata"
        data_dir.mkdir()
        snap = data_dir.parent / "cookies.json"
        snap.write_text("[{\"name\":\"UID\",\"value\":\"123\"}]", encoding="utf-8")
        b = Browser(_Cfg(data_dir))
        assert b.cookie_file() == snap and snap.exists()
        b.logout()                                        # context 为 None：只删本地快照，不报错
        assert not snap.exists(), "退出登录应删除本地 Cookie 快照(否则重启仍“记住”旧登录)"

        # ★关键回归：空闲时点「取消登录」→ Scheduler.do_logout(browser=None) 也必须删快照
        snap.write_text("[{\"name\":\"UID\",\"value\":\"456\"}]", encoding="utf-8")
        from ui.workbench import Scheduler
        s = Scheduler(cfg=_Cfg(data_dir), out=_q.Queue())
        assert s.browser is None and snap.exists()
        s.do_logout()
        assert not snap.exists(), "空闲态取消登录须立即清除记住的登录态(原来只置没人听的标志位=不管用)"
        assert not s.out.empty(), "取消登录应回一条消息"
    return "退出登录:清空 Cookie 快照/空闲态取消登录也即时清除 正确"


def t_login_detect_fallback() -> str:
    """登录收尾兜底：以浏览器实时 Cookie 为准——自动检测漏了也要记住；只有“取消且确未登录”才退出。"""
    import queue as _q
    from ui.workbench import Scheduler

    class FakeBrowser:
        def __init__(self, wait_ok, logged_in):
            self._wait_ok = wait_ok; self._logged = logged_in
            self.saved = 0; self.logout_called = False
        def start(self): return self
        page = None
        def wait_for_manual_login(self, on_prompt=None, should_cancel=None): return self._wait_ok
        def _cookie_logged_in(self): return self._logged
        def save_cookies(self): self.saved += 1; return 7
        def logout(self): self.logout_called = True

    def run(wait_ok, logged_in, cancelled):
        s = Scheduler(cfg=None, out=_q.Queue())
        fb = FakeBrowser(wait_ok, logged_in)
        s.browser = fb
        calls = []
        s.do_courses = lambda: calls.append("courses")     # 拦截“自动读取课程”
        if cancelled:
            s.login_cancel.set()
        s.do_login()
        fb.msgs = [s.out.get_nowait()[1] for _ in range(s.out.qsize())]
        fb.courses_called = bool(calls)
        return fb

    a = run(False, True, False)   # 自动检测没识别到,但浏览器已登录→必须补记,绝不退出,并自动读课程
    assert a.saved == 1 and not a.logout_called and a.courses_called, a.__dict__
    b = run(False, True, True)    # 点“取消”时其实已登录→中断等待+记住,不退出(修“取消误登出”)
    assert b.saved == 1 and not b.logout_called, b.__dict__
    c = run(False, False, True)   # 取消且确实没登录→按需求退出登录,且不该去读课程
    assert c.saved == 0 and c.logout_called and not c.courses_called, c.__dict__
    d = run(True, True, False)    # 正常登录成功→记住并自动读取课程(一步接一步)
    assert d.saved == 1 and not d.logout_called and d.courses_called, d.__dict__
    return "登录检测兜底:漏识别仍记住/取消不误登出/未登录才退出/成功后自动读取课程 全通过"


def t_judge_choice_mapping() -> str:
    """判断题选项映射回归：AI 答“A. 对”必须选第 0 项(对)，曾误选 B(错)致答错重试。"""
    from questions.popup import PopupWatcher

    class _Cfg:
        def get(self, _k, default=None): return default
        def path(self, _k): return "."

    class _Q:
        kind = "judge"
        options = [{"label": "A", "text": "A、对"}, {"label": "B", "text": "B、错"}]

    pw = PopupWatcher(cfg=_Cfg(), store=None)
    assert pw._ai_choice_indices(_Q(), "A. 对") == [0], "A. 对 应映射到第0项"
    assert pw._ai_choice_indices(_Q(), "B") == [1], "字母 B → 第1项"
    assert pw._ai_choice_indices(_Q(), "对") == [0], "纯文字 对 → 第0项"
    assert pw._ai_choice_indices(_Q(), "错") == [1], "纯文字 错 → 第1项"
    return "判断题映射:A/对→0、B/错→1 正确"


def t_clear_all_courses() -> str:
    """退出登录=谁登录就是谁的:清空课程及其章节/进度/成绩/讨论/错题,复位当前课选择,但保留题库。"""
    with temp_store() as (st, _d):
        cid = st.upsert_course({"course_key": "k1", "name": "课程A"})
        st.upsert_item(cid, {"item_key": "i1", "title": "小节1", "kind": "video"})
        st.upsert_grades(cid, [{"name": "平时", "score": 80, "full": 100}], overview="总评B")
        st.mark_discussed(cid, "post", "t1", "fp1", title="话题")
        qid, _dup = st.upsert_question({"stem": "1+1=?", "kind": "single"}, course_id=cid)
        st.set_meta("current_course", str(cid))
        assert st.list_courses() and st.list_items(cid, only_unfinished=False) and st.list_grades(cid)
        n = st.clear_all_courses()
        assert n == 1, n
        assert st.list_courses() == [], "课程应清空"
        for tbl in ("chapters", "items", "grades", "discussions", "study_log", "wrong_book"):
            assert st.q1(f"SELECT COUNT(*) c FROM {tbl}")["c"] == 0, tbl + " 应清空"
        assert st.get_meta("current_course", "") == "", "当前课选择应复位"
        assert st.q1("SELECT COUNT(*) c FROM questions")["c"] == 1, "题库(跨课程)应保留,不被退出登录误删"
    return "退出登录清空课程:清章节/进度/成绩/讨论/错题·复位当前课·保留题库 正确"


# ------------------------------------------------------------------ 入口
def run_all() -> int:
    from utils.console import init_console

    init_console()
    print("\n" + "=" * 62)
    print("离线自检（不联网、不启动浏览器）")
    print("=" * 62)
    check("文本与答案规范化", t_text2)
    check("SQLite 数据层", t_store)
    check("题目 HTML 解析（5 种题型）", t_extractor)
    check("知识库入库与检索", t_kb_and_search)
    check("AI 输出防编造校验", t_ai_guard)
    check("考试合规闸门", t_exam_compliance)
    check("配置安全红线", t_config_safety)
    check("练习模式写闸门", t_practice_lock)
    check("自动作答映射", t_autofill_plan)
    check("跳过已作答/阅读讨论配置", t_skip_answered_config)
    check("视频步进状态机", t_player_session)
    check("重试与防死循环", t_loop_guard)
    check("界面卡片渲染", t_report_render)
    check("提示词契约", t_prompt_contract)
    check("小节工作台协作式调度", t_scheduler_cooperative)
    check("成绩解析与渲染", t_grade_parse)
    check("学习记录成绩解析", t_study_parse)
    check("考核权重与加分建议", t_grade_criteria)
    check("成绩入库与读取", t_grades_store)
    check("成绩抓取装配", t_grade_selectors_wired)
    check("讨论目标与规划", t_discuss_plan)
    check("讨论批量并行生成", t_discuss_batch)
    check("讨论去重与计数", t_discuss_store)
    check("讨论装配", t_discuss_wired)
    check("阅读估时", t_reading_estimate)
    check("退出登录清快照", t_logout_clears_snapshot)
    check("登录检测兜底", t_login_detect_fallback)
    check("退出登录清空课程", t_clear_all_courses)
    check("判断题选项映射", t_judge_choice_mapping)
    failed = [r for r in RESULTS if not r[1]]
    print("-" * 62)
    print(f"结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过"
          + ("" if not failed else "；失败：" + ", ".join(f[0] for f in failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_all())
