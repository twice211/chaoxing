# -*- coding: utf-8 -*-
"""
video.player —— 视频正常播放监控（1.0 倍速、真实时长、断点记录）

合规红线（写在 SAFETY 里，不可配置）：
- 只以 1.0 倍速播放，绝不加速、绝不跳进度、绝不在后台“假播放”；
- 不注入任何用于欺骗平台挂机检测的事件（不伪造 visibility/焦点）；
- 若窗口失焦导致暂停，程序只提示“请把窗口保持在前台”，不绕过检测；
- 完成状态以平台/播放器真实 ended / progress 为准，读不到时交由用户确认。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from browser import actions as A
from browser.js import VIDEO_STATE_JS
from course.selectors import SELECTORS
from utils.logger import get_logger
from utils.retry import LoopGuardTripped
from utils.text import one_line
from utils.time_util import fmt_duration

log = get_logger("video.player")

FOCUS_JS = "() => ({hidden: !!document.hidden, focused: !!document.hasFocus(), vis: document.visibilityState || ''})"
FINISHED_TEXT_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const t = norm(document.body ? document.body.innerText : '').slice(0, 8000);
  return /已完成|已学完|学习完成|该任务点已完成/.test(t) ? 1 : 0;
}
"""


@dataclass
class WatchResult:
    complete: bool = False
    watched_sec: float = 0.0
    progress: float = 0.0
    reason: str = ""          # finished|stalled|no_video|timeout|user_stop|platform_done

    @property
    def ok(self) -> bool:
        return self.complete


class VideoWatcher:
    def __init__(self, browser: Any, cfg: Any, store: Any, popup: Any = None) -> None:
        self.browser = browser
        self.cfg = cfg
        self.store = store
        self.popup = popup          # questions.popup.PopupWatcher（可选）
        self.rate = min(1.0, float(cfg.get("VIDEO_PLAYBACK_RATE") or 1.0))

    # ------------------------------------------------------------ 定位播放器
    def find_video_frame(self, page: Any) -> Any | None:
        """跨 iframe 找到包含 <video> 的 frame。"""
        for frame in A.frames_of(page):
            state = A.js_eval(frame, VIDEO_STATE_JS, SELECTORS["video_tag"])
            if state and state.get("found"):
                return frame
        return None

    def state(self, frame: Any) -> dict[str, Any]:
        return dict(A.js_eval(frame, VIDEO_STATE_JS, SELECTORS["video_tag"]) or {})

    # ------------------------------------------------------------ 播控（等价于用户点击播放）
    def ensure_normal_speed(self, frame: Any) -> None:
        """强制 1.0 倍速 + 取消静音，保证是“正常播放”而不是加速刷课。"""
        js = r"""
        (rate) => {
          const v = document.querySelector('video');
          if (!v) return false;
          v.playbackRate = Math.min(rate, 1);   // 只允许 <=1.0
          if (v.muted) v.muted = false;
          return true;
        }
        """
        A.js_eval(frame, js, self.rate)

    @staticmethod
    def _probe(frame: Any) -> dict:
        """读取播放器真实状态（只读）。"""
        return A.js_eval(frame, r"""
        () => {
          const v = document.querySelector('video');
          if (!v) return {has: false};
          return {has: true, t: Number(v.currentTime || 0), d: Number(v.duration || 0),
                  paused: !!v.paused, ready: Number(v.readyState || 0),
                  rate: Number(v.playbackRate || 1), waiting: !!v.waiting,
                  ended: !!v.ended, preload: v.preload || ''};
        }
        """) or {"has": False}

    def start_play(self, frame: Any, page: Any = None) -> bool:
        """
        开始播放，并且**必须验证真的在播**才返回 True。

        学习通播放器是 video.js 且 preload=none：v.play() 返回 Promise，
        被自动播放策略拦截时不会同步抛错，所以只看异常会误判“已播放”。
        顺序：play() → 验证 → 点 .vjs-big-play-button → 验证 → 真实点击视频中心 → 验证。
        """
        self.ensure_normal_speed(frame)
        for step, action in enumerate(("play()", "大按钮", "视频中心")):
            if step == 0:
                A.js_eval(frame, "() => { const v = document.querySelector('video');"
                                  "if (v) { try { v.play(); } catch (e) {} } return true; }")
            elif step == 1:
                for sel in (".vjs-big-play-button", ".vjs-play-control", "[class*='play-btn']",
                            ".prism-play-btn"):
                    if A.click_first(frame, [sel], timeout=1500):
                        break
            else:
                box = None
                try:
                    box = frame.locator("video").first.bounding_box(timeout=2000)
                except Exception:
                    box = None
                if box and page is not None:
                    try:
                        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    except Exception:
                        pass
            for _ in range(8):                    # 最多等 4 秒，确认状态变化
                time.sleep(0.5)
                st = self._probe(frame)
                if st.get("has") and not st.get("paused") and int(st.get("ready") or 0) >= 2:
                    self.ensure_normal_speed(frame)
                    log.info("播放已确认（方式：%s）", action)
                    return True
            log.debug("尝试 %s 后仍未开始播放", action)
        return False

    def pause_play(self, frame: Any) -> bool:
        """真正暂停播放器（video.pause()），并验证已暂停。用户点“暂停”时用。

        与 start_play 对称：只把状态机 paused 置位是不够的——学习通的 <video>
        会继续自动播放，必须调用它的 pause()。
        """
        for step, action in enumerate(("pause()", "播放按钮")):
            if step == 0:
                A.js_eval(frame, "() => { const v = document.querySelector('video');"
                                  "if (v) { try { v.pause(); } catch (e) {} } return true; }")
            else:
                # 有的 video.js 皮肤用按钮切状态；点主控制按钮兜底
                for sel in (".vjs-play-control", ".prism-play-btn", ".vjs-big-play-button"):
                    if A.click_first(frame, [sel], timeout=1200):
                        break
            for _ in range(6):
                time.sleep(0.4)
                st = self._probe(frame)
                if st.get("has") and st.get("paused"):
                    log.info("暂停已确认（方式：%s）", action)
                    return True
        log.debug("尝试暂停后播放器仍未进入暂停态")
        return False

    def platform_says_done(self, page: Any) -> bool:
        for frame in A.frames_of(page):
            if A.js_eval(frame, FINISHED_TEXT_JS):
                return True
        return bool(A.first_element(page, SELECTORS["video_finished_flag"]))

    # ------------------------------------------------------------ 主循环
    def watch(self, page: Any, item: dict[str, Any] | None = None, on_tick: Any = None) -> WatchResult:
        """
        观看当前小节的视频直到结束。

        参数 item 为数据库中的学习项（用于写进度）。返回 WatchResult。
        """
        frame = self.find_video_frame(page)
        if frame is None:
            return WatchResult(complete=False, reason="no_video")
        st0 = self.state(frame)
        duration = float(st0.get("duration") or 0)
        meta_waited = 0.0
        item_id = item.get("id") if item else None
        if item_id:
            self.store.log_study(item_id, "start", f"视频时长 {fmt_duration(duration)}", duration)
        self.start_play(frame, page)

        poll = max(2.0, float(self.cfg.get("VIDEO_POLL_SEC") or 10))
        stall_limit = max(20.0, float(self.cfg.get("VIDEO_STALL_TIMEOUT_SEC") or 120))
        max_wait = (duration * 1.6 + 300) if duration else 3600 * 2
        deadline = time.time() + max_wait
        last_cur = float(st0.get("current") or 0)
        last_move = time.time()
        watched = 0.0
        resume_tries = 0
        warned_focus = False
        guard_steps = 0

        print(f"\n[步骤] 开始以 {self.rate:.1f}x 正常速度播放"
              f"（时长 {fmt_duration(duration) if duration else '正在获取…'}）。请保持该窗口在前台，勿切走。",
              flush=True)
        duration_shown = bool(duration)
        while time.time() < deadline:
            guard_steps += 1
            if guard_steps > 4000:  # 双保险：防止轮询循环失控
                return WatchResult(complete=False, watched_sec=watched, reason="timeout")
            try:
                st = self.state(frame)
            except Exception as exc:
                log.warning("读取播放器状态失败：%s", exc)
                st = {}
            if not st or not st.get("found"):
                # 播放器可能因页面跳转消失：若平台已标注完成则视为完成
                if item_id and self.platform_says_done(page):
                    self.store.finish_item(item_id, "平台标注已完成")
                    return WatchResult(complete=True, watched_sec=watched, progress=1.0, reason="platform_done")
                time.sleep(poll)
                continue

            cur = float(st.get("current") or 0)
            dur = float(st.get("duration") or 0) or float(duration or 0)
            if not dur:                       # preload=none 时 duration 要等加载完才有
                meta_waited += poll
                if meta_waited > 45:
                    err("45 秒内拿不到视频时长，可能没真正开始播放。请把窗口切回前台后重试。")
                    return WatchResult(complete=False, watched_sec=watched, progress=progress,
                                       reason="no_metadata")
                if not self.start_play(frame, page):
                    log.debug("等待时长元数据期间再次尝试播放失败")
                continue
            if dur and not duration_shown:          # 元数据加载完成后补一次真实时长
                duration_shown = True
                duration = dur
                print(f"\n[信息] 视频时长获取成功：{fmt_duration(dur)}（预计还需 "
                      f"{fmt_duration(max(0.0, dur - cur))}）", flush=True)
            progress = min(1.0, cur / dur) if dur else 0.0
            delta = cur - last_cur
            if delta > 0:
                watched += min(delta, poll * 2)
                last_cur = cur
                last_move = time.time()
                self.away_note = False          # 你回到页面后，清除“失焦”提示标记
                if self.popup is not None:
                    try:
                        self.popup.notify_progress()      # 用户答完题、播放恢复
                    except Exception:
                        pass
                if getattr(self, "waiting_note", False):
                    self.waiting_note = False
                    print("[信息] 已检测到播放恢复，继续记录进度。", flush=True)
            if delta < 0.5:  # 进度未推进（暂停/卡住/缓冲/等你作答）
                aw = self.popup is not None and getattr(self.popup, "awaiting", False)
                if aw:
                    wait_sec = time.time() - (getattr(self.popup, "awaiting_since", 0) or time.time())
                    if wait_sec < 300:                    # 最多等你 5 分钟作答
                        if not getattr(self, "waiting_note", False):
                            self.waiting_note = True
                            print("[注意] 随堂题出现，播放已暂停 —— 请在浏览器窗口自己作答并提交，"
                                  "答完会自动继续（最长等你 5 分钟）。", flush=True)
                        last_move = time.time()           # 不计入停滞
                        if item_id:
                            self.store.log_study(item_id, "waiting_answer",
                                                 f"等待作答 {wait_sec:.0f}s", watched)
                        time.sleep(poll)
                        continue
                    self.popup.awaiting = False
                    print("[注意] 等待作答超时，继续后面的流程（该节可能仍未完成任务点）。", flush=True)
                # 平台规则：页面被切走/失焦就暂停。这里只提示，不伪造可见性或焦点。
                focus_now = A.js_eval(frame, FOCUS_JS) or {}
                if focus_now.get("hidden") or focus_now.get("focused") is False:
                    away = time.time() - last_move
                    if 20 < away < 600:
                        if not getattr(self, "away_note", False):
                            self.away_note = True
                            print("[注意] 学习通检测到页面被切走/失焦，已自动暂停播放。\n"
                                  "       请把鼠标移回播放页面并把该窗口切到前台，播放会自动继续。\n"
                                  "       程序不会伪造页面状态来绕过这个检测（课程承诺书与平台规则均禁止）。",
                                  flush=True)
                        if item_id:
                            self.store.log_study(item_id, "page_away", f"页面失焦 {away:.0f}s", watched)
                        time.sleep(poll)
                        continue
                    if away >= 600:
                        print("[注意] 页面离开超过 10 分钟，本节先停止；回来后重新运行即可续学。", flush=True)
                        return WatchResult(complete=False, watched_sec=watched, progress=progress,
                                           reason="page_away")
                if time.time() - last_move > stall_limit:
                    focus = focus_now
                    if not warned_focus and focus.get("hidden"):
                        print("[注意] 页面处于后台，学习通会暂停播放。请将学习窗口切回前台（程序不会伪造前台状态）。")
                        warned_focus = True
                    if resume_tries < 3:
                        resume_tries += 1
                        print(f"[步骤] 检测到播放停滞，尝试继续播放（第 {resume_tries}/3 次）…", flush=True)
                        if not self.start_play(frame, page):
                            err("无法开始播放：请你在浏览器里手动点一下播放按钮，"
                                "然后重新运行本命令（程序不会绕过平台的任何限制）。")
                            return WatchResult(complete=False, watched_sec=watched,
                                               progress=progress, reason="need_manual")
                        last_move = time.time()
                        continue
                    return WatchResult(complete=False, watched_sec=watched, progress=progress, reason="stalled")
            else:
                resume_tries = 0
                warned_focus = False

            if item_id:
                self.store.update_progress(item_id, progress, watched)
                self.store.log_study(item_id, "progress", f"{progress * 100:.1f}%", watched)
            if self.popup is not None and self.cfg.get("POPUP_DETECT", True):
                try:
                    self.popup.check(page)      # 出现弹题：把题目与 AI 解析打到终端
                except Exception as exc:
                    log.debug("弹题检查异常：%s", exc)
            if on_tick:
                try:
                    on_tick(progress, watched, dur)
                except Exception:
                    log.debug("on_tick 回调异常", exc_info=True)
                print(f"\r    播放进度 {progress * 100:5.1f}%  已学 {fmt_duration(watched)}  ",
                      end="", flush=True)

            if st.get("ended") or progress >= 0.985:
                print()
                if item_id:
                    self.store.finish_item(item_id, f"播放完成（观看 {fmt_duration(watched)}）")
                return WatchResult(complete=True, watched_sec=watched, progress=1.0, reason="finished")
            time.sleep(poll)
        print()
        return WatchResult(complete=False, watched_sec=watched, reason="timeout")

    # ------------------------------------------------------------ 可暂停的步进状态机（小窗用；不改上面 watch()）
    def start_session(self, page: Any, item: dict | None = None, on_tick: Any = None,
                      poll: float | None = None, stall_limit: float | None = None) -> "VideoWatcher.Session":
        poll = poll if poll is not None else max(2.0, float(self.cfg.get("VIDEO_POLL_SEC") or 10))
        stall = stall_limit if stall_limit is not None else max(20.0, float(self.cfg.get("VIDEO_STALL_TIMEOUT_SEC") or 120))
        return self.Session(self, page, item, on_tick=on_tick, poll=poll, stall_limit=stall)

    class Session:
        """视频观看的“心跳步进”状态机，供小窗按定时器驱动。

        与阻塞式 watch() 不同：tick() 每次只推进一小步，等待节奏由调用方决定，
        所以用户随时点“暂停/停止/切标签”都能立刻响应，不会被独占线程卡住。
        合规约束与 watch() 一致：只 1.0 倍速、不伪造前台、完成以播放器/平台真实状态为准。
        """

        def __init__(self, watcher: "VideoWatcher", page: Any, item: dict | None = None,
                     on_tick: Any = None, poll: float = 2.0, stall_limit: float = 120.0) -> None:
            self.w = watcher
            self.page = page
            self.item_id = (item or {}).get("id")
            self.on_tick = on_tick
            self.poll = poll
            self.stall_limit = stall_limit
            self.progress = 0.0
            self.watched = 0.0
            self.duration = 0.0
            self.status = "watching"          # watching|await|away|done|fail
            self.notice = ""
            self.done = False
            self.paused = False
            self._meta = 0.0
            self._resume = 0
            self._away_note = False
            self._last_move = time.time()
            self.frame = watcher.find_video_frame(page)
            if self.frame is None:
                self.done = True
                self.status = "fail"
                self.notice = "no_video"
                return
            st0 = watcher.state(self.frame)
            self.duration = float(st0.get("duration") or 0)
            self._last_cur = float(st0.get("current") or 0)
            watcher.start_play(self.frame, page)
            if self.item_id:
                watcher.store.log_study(self.item_id, "start",
                                        f"视频时长 {fmt_duration(self.duration)}", self.duration)

        def stop(self) -> None:
            self.done = True
            self.status = "fail"
            self.notice = "user_stop"
            try:
                if self.frame is not None:
                    self.w.pause_play(self.frame)   # 停止时也把播放器真暂停
            except Exception:
                pass

        def pause(self) -> None:
            self.paused = True
            self.notice = "已暂停"
            ok = False
            try:
                ok = self.w.pause_play(self.frame)     # 真正暂停浏览器播放器（不只置标记）
            except Exception as exc:
                log.debug("暂停播放器异常：%s", exc)
            if not ok:
                self.notice = "已暂停（播放器未能确认，可能仍在缓冲）"

        def resume(self) -> None:
            self.paused = False
            self._last_move = time.time()               # 防止刚恢复就被判“停滞”
            try:
                self.w.start_play(self.frame, self.page)  # 真正继续播放
            except Exception as exc:
                log.debug("继续播放异常：%s", exc)

        def tick(self) -> str:
            if self.done:
                return self.status
            if self.paused:
                return self.status
            w = self.w
            try:
                st = w.state(self.frame)
            except Exception as exc:
                log.warning("读取播放器状态失败：%s", exc)
                st = {}
            if not st or not st.get("found"):
                if self.item_id and w.platform_says_done(self.page):
                    self._finish(True, "platform_done")
                else:
                    self.notice = "等待播放器…"
                return self.status
            cur = float(st.get("current") or 0)
            dur = float(st.get("duration") or 0) or float(self.duration or 0)
            if not dur:
                self._meta += self.poll
                if self._meta > 45:
                    self._finish(False, "no_metadata")
                    return self.status
                w.start_play(self.frame, self.page)
                self.notice = "获取时长…"
                return self.status
            self.duration = dur
            self.progress = min(1.0, cur / dur)
            delta = cur - self._last_cur
            now = time.time()
            if delta > 0:
                self.watched += min(delta, self.poll * 2)
                self._last_cur = cur
                self._last_move = now
                self._resume = 0
                self._away_note = False
                if w.popup is not None:
                    try:
                        w.popup.notify_progress()
                    except Exception:
                        pass
            if delta < 0.5:
                aw = w.popup is not None and getattr(w.popup, "awaiting", False)
                if aw:
                    self.status = "await"
                    self.notice = "随堂题待作答（练习模式才会自动作答）"
                    if self.item_id:
                        w.store.log_study(self.item_id, "waiting_answer", "等待作答", self.watched)
                    return self.status
                focus = A.js_eval(self.frame, FOCUS_JS) or {}
                if focus.get("hidden") or focus.get("focused") is False:
                    away = now - self._last_move
                    self.status = "away"
                    self.notice = "窗口失焦，学习通已暂停——请把该窗口切回前台"
                    if away >= 600:
                        self._finish(False, "page_away")
                    elif self.item_id and away > 20:
                        w.store.log_study(self.item_id, "page_away", f"页面失焦 {away:.0f}s", self.watched)
                    return self.status
                if now - self._last_move > self.stall_limit:
                    if self._resume < 3:
                        self._resume += 1
                        self.notice = f"检测到停滞，尝试续播（{self._resume}/3）"
                        if w.start_play(self.frame, self.page):
                            self._last_move = now
                        else:
                            self._finish(False, "need_manual")
                    else:
                        self._finish(False, "stalled")
                    return self.status
            self.status = "watching"
            if self.item_id:
                w.store.update_progress(self.item_id, self.progress, self.watched)
                w.store.log_study(self.item_id, "progress", f"{self.progress * 100:.1f}%", self.watched)
            if w.popup is not None and w.cfg.get("POPUP_DETECT", True):
                try:
                    w.popup.check(self.page)
                except Exception as exc:
                    log.debug("弹题检查异常：%s", exc)
            if self.on_tick:
                try:
                    self.on_tick(self.progress, self.watched, self.duration)
                except Exception:
                    log.debug("on_tick 回调异常", exc_info=True)
            self.notice = f"{self.progress * 100:.1f}% ｜ 已学 {fmt_duration(self.watched)}"
            if st.get("ended") or self.progress >= 0.985:
                self._finish(True, "finished")
            return self.status

        def _finish(self, complete: bool, reason: str) -> None:
            if complete and self.item_id:
                self.w.store.finish_item(self.item_id, f"播放完成（观看 {fmt_duration(self.watched)}）")
            self.done = True
            self.status = "done" if complete else "fail"
            self.notice = reason

    # ------------------------------------------------------------ 非视频任务（文档/PPT）
    def read_document(self, page: Any, item: dict[str, Any] | None = None,
                      min_seconds: float = 45.0, max_seconds: float = 240.0) -> WatchResult:
        """
        文档/PPT 任务：保持页面打开并真实停留（可顺带把正文抓进知识库）。
        停留结束后：平台显示已完成则记完成；否则提示用户人工确认，绝不伪造完成状态。
        """
        item_id = item.get("id") if item else None
        try:
            text_len = int(A.js_eval(page, "() => (document.body.innerText||'').length") or 0)
        except Exception:
            text_len = 0
        need = min(max_seconds, max(min_seconds, text_len / 12.0))
        print(f"\n[步骤] 文档/课件任务：将在页面停留 {need:.0f} 秒供你阅读（正文 {text_len} 字符）。")
        waited = 0.0
        step = 10.0
        while waited < need:
            if item_id:
                self.store.update_progress(item_id, min(0.99, waited / need), waited)
            time.sleep(step)
            waited += step
            print(f"\r    已停留 {waited:.0f}/{need:.0f} 秒  ", end="", flush=True)
        print()
        if item_id and self.platform_says_done(page):
            self.store.finish_item(item_id, f"页面停留 {fmt_duration(waited)} 后平台标注完成")
            return WatchResult(complete=True, watched_sec=waited, progress=1.0, reason="platform_done")
        print("[注意] 平台未完成标记未检测到。是否需要人工确认该任务点已完成？")
        from utils.console import confirm

        if confirm("请在学习通页面确认状态后回答（不确定请直接回车选 N）", default_no=True):
            if item_id:
                self.store.finish_item(item_id, "用户人工确认已完成")
            return WatchResult(complete=True, watched_sec=waited, reason="user_confirm")
        return WatchResult(complete=False, watched_sec=waited, reason="need_manual")