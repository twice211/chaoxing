# -*- coding: utf-8 -*-
"""tools.cdp_discuss —— 经本机调试端口(127.0.0.1:9222)接管已登录浏览器,协作读话题/写回复/发帖。

用法(全部只服务本机端口;不点任何“发表/回复”以外的按钮):
  python tools/cdp_discuss.py list
  python tools/cdp_discuss.py reply <topic_key> <正文文本文件>
  python tools/cdp_discuss.py post <标题文件> <正文文件>
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

CDP = "http://127.0.0.1:9222"
CHAOXING_HINTS = ("chaoxing.com", "xuexitong.com", "cx.cn")


def _page(ctx):
    for pg in ctx.pages:
        if any(h in (pg.url or "") for h in CHAOXING_HINTS):
            return pg
    return ctx.pages[0] if ctx.pages else None


def _board_frame(pg):
    """找讨论区 iframe(groupweb topicList);没有则点课程左导航「讨论」再等。"""
    def scan():
        for fr in pg.frames:
            u = (fr.url or "").lower()
            if "groupweb" in u and "topic" in u:
                return fr
        return None
    fr = scan()
    if fr is not None:
        return fr
    for target in ("a[dataname='tl']", "a:has-text('讨论')", "[dataname='tl']"):
        try:
            el = pg.query_selector(target)
            if el and el.is_visible():
                el.evaluate("node => node.click()")
                break
        except Exception:
            continue
    time.sleep(4.0)
    return scan()


def _runner():
    from course.discussion import DiscussionRunner
    return DiscussionRunner(browser=None, cfg=None, store=None, crawler=None, engine=None)


def cmd_list(pg):
    from course.discussion import TOPICS_JS
    from browser import actions as A
    fr = _board_frame(pg)
    if fr is None:
        return {"ok": False, "why": "没找到讨论区 iframe(groupweb topicList)"}
    rows = A.js_eval(fr, TOPICS_JS) or []
    return {"ok": True, "count": len(rows), "topics": rows}


def _detail_open(fr):
    """详情弹层里应有“回复话题”textarea。"""
    try:
        ta = fr.query_selector("textarea[placeholder*='回复']")
        return ta if ta and ta.is_visible() else None
    except Exception:
        return None


def cmd_reply(pg, key: str, text: str):
    fr = _board_frame(pg)
    if fr is None:
        return {"ok": False, "why": "没找到讨论区页面"}
    if _detail_open(fr) is not None or fr.evaluate("() => (document.body.innerText||'').includes('话题详情')"):
        fr.evaluate("() => location.reload()")   # 统一从列表出发,避免弹层错位发错楼
        time.sleep(3.5)
        if fr.is_detached():
            fr = _board_frame(pg)
            if fr is None:
                return {"ok": False, "why": "刷新后讨论区帧丢失"}
    if _detail_open(fr) is None:
        li = fr.query_selector(f"li[data-uuid='{key}']")
        if li is None:
            return {"ok": False, "why": f"列表里没有该话题(key={key[:12]})"}
        link = li.query_selector("a.topicli_link") or li
        try:
            link.scroll_into_view_if_needed()
            link.click(timeout=6000)
        except Exception:
            try:
                link.evaluate("node => node.click()")
            except Exception as exc:
                return {"ok": False, "why": f"点开话题失败:{exc}"}
        for _ in range(6):
            time.sleep(0.7)
            if _detail_open(fr) is not None:
                break
    ta = _detail_open(fr)
    if ta is None:
        return {"ok": False, "why": "详情里没出现“回复话题”输入框(可能弹层未就绪)"}
    before = fr.evaluate(r"""() => { const m=(document.body.innerText||'').match(/共(\d+)条回复/); return m?parseInt(m[1]):null; }""")
    try:
        ta.fill(text)
    except Exception as exc:
        return {"ok": False, "why": f"填回复框失败:{exc}"}
    time.sleep(0.5)
    btn = None
    for sel in (".topicDetail .addReply", ".addReply", ".replyBtn", ".detail_bottom .btnBlue"):
        try:
            el = fr.query_selector(sel)
            if el and el.is_visible():
                btn = el
                break
        except Exception:
            continue
    if btn is None:
        return {"ok": False, "filled": True, "why": "已填入但没找到回复按钮,请在窗口点『回复』"}
    try:
        btn.click(timeout=5000)
    except Exception:
        btn.evaluate("node => node.click()")
    after = before
    for _ in range(6):
        time.sleep(1.0)
        after = fr.evaluate(r"""() => { const m=(document.body.innerText||'').match(/共(\d+)条回复/); return m?parseInt(m[1]):null; }""")
        if before is None or (after is not None and before is not None and after > before):
            break
    verified = (before is None and after is not None) or (after is not None and before is not None and after > before)
    return {"ok": bool(verified), "submitted": True, "count_before": before, "count_after": after,
            "why": "回复数已+1 ✓" if verified else "已点回复按钮但回复数未变,请到窗口确认"}


def cmd_post(pg, title: str, body: str):
    r = _runner()
    fr = _board_frame(pg)
    if fr is None:
        return {"ok": False, "why": "没找到讨论区页面"}
    if not r.open_new_post(pg):
        return {"ok": False, "why": "没找到/点不开“新建话题”"}
    time.sleep(1.2)
    res = r.fill_editor(pg, body, title=title)
    if not res.get("body"):
        return {"ok": False, "why": "发帖表单里没找到正文编辑器"}
    time.sleep(0.6)
    okc, why2 = r.submit_visible(pg, "new_post")
    return {"ok": bool(okc), "title_filled": bool(res.get("title")), "submitted": bool(okc),
            "why": why2 if not okc else "已点发表提交(请在窗口确认发布成功)"}


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    action = argv[1]
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0] if browser.contexts else None
        if ctx is None:
            print(json.dumps({"ok": False, "why": "端口已开但没有浏览器上下文"}, ensure_ascii=False))
            return 1
        pg = _page(ctx)
        if pg is None:
            print(json.dumps({"ok": False, "why": "浏览器里没有可接管的页面,请先在应用里打开学习通课程"}, ensure_ascii=False))
            return 1
        if action == "list":
            out = cmd_list(pg)
        elif action == "reply" and len(argv) >= 4:
            out = cmd_reply(pg, argv[2], Path(argv[3]).read_text(encoding="utf-8").strip())
        elif action == "post" and len(argv) >= 4:
            title = Path(argv[2]).read_text(encoding="utf-8").strip().splitlines()[0]
            body = Path(argv[3]).read_text(encoding="utf-8").strip()
            out = cmd_post(pg, title, body)
        else:
            print(__doc__)
            return 2
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
