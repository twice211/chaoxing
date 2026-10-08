# -*- coding: utf-8 -*-
"""演示：本地“视频页 + 定时随堂弹题”，在 PRACTICE_MODE 下由 PopupWatcher 自动作答并提交。

不联网、不碰真实账号；用桩 AI。
运行：
    python tools/demo_popup.py            # 无头：打印过程与 PASS/FAIL（自检）
    python tools/demo_popup.py --show     # 弹出浏览器：直观看“弹题→自动勾选→提交”
"""
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from ai.responder import Answer  # noqa: E402
from database.store import Store  # noqa: E402
from questions.popup import PopupWatcher  # noqa: E402

HTML = pathlib.Path(__file__).resolve().parents[1] / "docs/demo/lesson_popup_demo.html"


class Cfg:
    def __init__(self, d): self.d = d
    def get(self, k, default=None): return self.d.get(k, default)


class FakeEngine:
    def __init__(self): self.ai = type("A", (), {"enabled": True})()
    def answer(self, question, evidence=None, mode="", chapter="", qno="", **kw):
        return Answer(answer="B", analysis="叠加定理只适用于线性电路（演示桩）", knowledge="叠加定理",
                      evidence="演示：本地资料无直接依据", confidence="中", needs_human=False)


def main() -> int:
    show = "--show" in sys.argv
    with tempfile.TemporaryDirectory() as d:
        store = Store(db_path=pathlib.Path(d) / "demo.db")
        cfg = Cfg({"PRACTICE_MODE": True, "PRACTICE_ALLOW_SUBMIT": True,
                   "REAL_EXAM_DOMAINS": ["chaoxing.com", "xuexitong.com"],
                   "KB_TOP_K": 5, "POPUP_DETECT": True})
        popup = PopupWatcher(cfg=cfg, store=store, engine=FakeEngine(), kb=None,
                             min_interval_sec=0.0, on_out=lambda lvl, txt: print(f"    [{lvl}] {txt}"))
        with sync_playwright() as p:
            b = p.chromium.launch(headless=not show, slow_mo=500 if show else 0)
            pg = b.new_page()
            pg.goto(HTML.resolve().as_uri())
            try:  # 等随堂弹题真正可见后再进入检测，避免抓到“刚出现的一瞬”被去重跳过
                pg.wait_for_function("()=>!document.getElementById('pop').classList.contains('hidden')",
                                     timeout=12000)
            except Exception:
                pass
            submitted = False
            steps = 20 if show else 12
            for _ in range(steps):
                popup.check(pg)
                try:
                    submitted = pg.evaluate("()=>document.getElementById('result').textContent==='SUBMITTED'")
                except Exception:
                    submitted = False
                if submitted:
                    break
                time.sleep(1 if show else 0.5)
            checked = pg.evaluate("()=>{const e=document.getElementById('cb1_1');return !!(e&&e.checked)}")
            if show:
                print("    （窗口停留 3 秒供你观察后自动关闭）")
                time.sleep(3)
            b.close()
        store.close()
        res = "PASS" if (submitted and checked) else "FAIL"
        print(f"DEMO {res}  弹题自动勾选 B={checked}，已提交={submitted}")
        return 0 if res == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
