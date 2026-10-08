# -*- coding: utf-8 -*-
"""
browser.driver —— Playwright 会话管理（持久化登录态 + 用户本人完成验证）

合规要点：
- 只打开浏览器并**等待用户本人**完成登录 / 验证码 / 身份验证；
- 不识别、不绕过、不提交任何验证码；不代填账号密码；
- 使用 launch_persistent_context 保存登录态，重启免重复登录；
- 不注入任何“伪装/绕过检测”的脚本。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable

import requests
from playwright.sync_api import BrowserContext, Error as PlaywrightError, Page, sync_playwright

from course.selectors import SELECTORS, apply_overrides
from utils.logger import get_logger
from utils.time_util import now_str

log = get_logger("browser.driver")


def cookie_snapshot_path(cfg: Any) -> Path:
    """本地 Cookie 快照路径（Browser 与 Scheduler 共用，未启动浏览器也能拿到）。"""
    return Path(cfg.path("USER_DATA_DIR")).parent / "cookies.json"


class LoginRequired(RuntimeError):
    """需要用户本人完成登录/验证。"""


class Browser:
    """一个可复用的浏览器会话（持有持久化用户目录）。"""

    def __init__(self, cfg: Any, store: Any | None = None, headless: bool = False) -> None:
        self.cfg = cfg
        self.store = store
        # 调试用的无头模式：只有在本地已存在登录 Cookie 快照时才生效，
        # 绝不用于“绕过登录”——没登录时依然会要求你本人在可见窗口登录。
        self.headless = bool(headless)
        self.pw = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._pages: list[Page] = []
        apply_overrides(getattr(cfg, "SELECTOR_OVERRIDES", None) if not isinstance(cfg, dict) else cfg.get("SELECTOR_OVERRIDES"))

    # ------------------------------------------------------------ 生命周期
    def start(self) -> "Browser":
        """启动浏览器。必须在创建它的线程内使用（Playwright 线程约束）。"""
        if self.context is not None:
            return self
        user_data = Path(self.cfg.path("USER_DATA_DIR"))
        user_data.mkdir(parents=True, exist_ok=True)
        log.info("启动浏览器（持久化目录：%s）", user_data)
        self.pw = sync_playwright().start()
        browser_type = getattr(self.pw, str(self.cfg.get("BROWSER_CHANNEL", "chromium")).lower(), self.pw.chromium)
        allow_headless = self.headless and self.cookie_file().exists()
        if self.headless and not allow_headless:
            print("[注意] 无本地登录 Cookie，调试模式仍用可见窗口（登录必须由你本人完成）")
        self.context = browser_type.launch_persistent_context(
            user_data_dir=str(user_data),
            headless=allow_headless,  # 默认可见：登录/验证码需用户本人操作
            viewport=self.cfg.get("WINDOW_SIZE"),
            slow_mo=int(self.cfg.get("SLOW_MO_MS") or 0),
            accept_downloads=True,
            args=self._cdp_args(),
        )
        self.context.set_default_timeout(int(self.cfg.get("PAGE_LOAD_TIMEOUT_MS") or 60000))
        self.context.set_default_navigation_timeout(int(self.cfg.get("PAGE_LOAD_TIMEOUT_MS") or 60000))
        self.restore_cookies()          # ★ 关键：学习通很多是会话级 Cookie，这里显式回灌
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self._pages = list(self.context.pages)
        return self

    def _cdp_args(self) -> list[str]:
        """BROWSER_CDP_PORT>0 时开放“本机”调试端口(仅 127.0.0.1),便于外部工具协作;0=关闭。"""
        try:
            cdp = int(self.cfg.get("BROWSER_CDP_PORT") or 0)
        except (TypeError, ValueError):
            cdp = 0
        if cdp <= 0:
            return []
        log.info("浏览器本机调试端口已开启:127.0.0.1:%s", cdp)
        return [f"--remote-debugging-port={cdp}", "--remote-debugging-address=127.0.0.1"]

    def stop(self) -> None:
        self.save_cookies()             # 关闭前落盘，下次免登录
        try:
            if self.context:
                self.context.close()
        except Exception as exc:  # 关闭失败不影响退出
            log.debug("关闭浏览器上下文失败：%s", exc)
        finally:
            self.context = None
            self.page = None
        try:
            if self.pw:
                self.pw.stop()
        except Exception:
            log.debug("停止 playwright 失败", exc_info=True)
        self.pw = None

    def __enter__(self) -> "Browser":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # ------------------------------------------------------------ Cookie 持久化
    def cookie_file(self) -> Path:
        return Path(self.cfg.path("USER_DATA_DIR")).parent / "cookies.json"

    def logout(self) -> None:
        """
        退出登录：清空当前上下文 Cookie 并删除本地快照，使“记住登录”失效。
        下次使用需由用户本人重新登录。
        """
        if self.context is not None:
            try:
                self.context.clear_cookies()
                log.info("已清空浏览器 Cookie（退出登录）")
            except Exception as exc:
                log.debug("清空 Cookie 失败：%s", exc)
        try:
            path = self.cookie_file()
            if path.exists():
                path.unlink()
                log.info("已删除本地 Cookie 快照：%s", path)
        except Exception as exc:
            log.debug("删除 Cookie 快照失败：%s", exc)

    def save_cookies(self) -> int:
        """把当前 Cookie 快照到本地文件（登录态跨进程复用的关键）。"""
        if self.context is None:
            return 0
        try:
            cookies = self.context.cookies()
        except Exception as exc:
            log.debug("读取 Cookie 失败：%s", exc)
            return 0
        if not cookies:
            return 0
        try:
            path = self.cookie_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cookies, ensure_ascii=False, indent=1), encoding="utf-8")
            log.info("已保存 %s 条 Cookie 到 %s", len(cookies), path)
            return len(cookies)
        except Exception as exc:
            log.warning("保存 Cookie 失败：%s", exc)
            return 0

    def restore_cookies(self) -> int:
        """启动时回灌上次保存的 Cookie；失效则忽略，仍会请你登录一次。"""
        if self.context is None:
            return 0
        path = self.cookie_file()
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("读取 Cookie 快照失败：%s", exc)
            return 0
        cleaned = []
        for c in data if isinstance(data, list) else []:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            item = dict(c)
            exp = item.get("expires")
            if exp is None or (isinstance(exp, (int, float)) and exp and exp < 0):
                item.pop("expires", None)          # 会话级 Cookie：按会话注入
            else:
                try:
                    item["expires"] = float(exp)
                except Exception:
                    item.pop("expires", None)
            cleaned.append(item)
        try:
            self.context.add_cookies(cleaned)
            log.info("已回灌 %s 条 Cookie（来自 %s）", len(cleaned), path.name)
            return len(cleaned)
        except Exception as exc:
            log.warning("回灌 Cookie 失败：%s", exc)
            return 0

    # ------------------------------------------------------------ 页签
    def new_tab(self, url: str = "") -> Page:
        self.start()
        assert self.context is not None
        page = self.context.new_page()
        page.set_default_timeout(int(self.cfg.get("PAGE_LOAD_TIMEOUT_MS") or 60000))
        self._pages.append(page)
        if url:
            page.goto(url, wait_until="domcontentloaded")
        return page

    def close_tab(self, page: Page) -> None:
        try:
            if page in self._pages:
                self._pages.remove(page)
            page.close()
        except Exception:
            log.debug("关闭页签失败", exc_info=True)

    def tabs(self) -> list[Page]:
        """返回所有存活页签；点击课程卡片常会新开页签，所以以 context.pages 为准。"""
        pages: list[Page] = []
        try:
            if self.context is not None:
                pages = [pg for pg in self.context.pages if not pg.is_closed()]
        except Exception:
            pages = []
        if not pages:
            pages = [pg for pg in self._pages if not pg.is_closed()]
        return pages

    # ------------------------------------------------------------ 登录态
    def cookie_map(self) -> dict[str, str]:
        self.start()
        assert self.context is not None
        try:
            return {c["name"]: c["value"] for c in self.context.cookies()}
        except PlaywrightError as exc:
            log.warning("读取 cookie 失败：%s", exc)
            return {}

    def _cookie_logged_in(self) -> bool:
        cookies = self.cookie_map()
        uid = cookies.get("UID") or cookies.get("uid") or cookies.get("_uid")
        return bool(uid and str(uid) not in ("-1", "0", "null", "undefined"))

    def looks_logged_in(self, verify: bool = False) -> bool:
        """
        Cookie + 页面标志双重判断（只读判断，不做任何验证绕过）。

        verify=True 时额外做一次“访问个人空间是否被弹回登录页”的核验，
        避免学习通只下发埋点 cookie 造成的“已登录”误判。
        """
        if not self._cookie_logged_in():
            return False
        page = self.page
        if page is None:
            return True
        try:
            for sel in SELECTORS["login_indicator"]:
                if page.query_selector(sel):
                    return True
        except PlaywrightError:
            pass
        if verify:
            from browser.actions import safe_goto   # 延迟导入，避免循环依赖

            home = self.cfg.get("CHAOXING_HOME_URL") or "https://i.chaoxing.com"
            if safe_goto(page, f"{home.rstrip('/')}/base", attempts=2):
                try:
                    final = str(page.url or "")
                except PlaywrightError:
                    final = ""
                if "passport" in final or "/login" in final:
                    return False
        # 找不到标志元素时，只要 cookie 判定为已登录也放行（学习通改版兼容）
        return True

    def wait_for_manual_login(self, timeout: int | None = None, on_prompt: Any = None,
                            should_cancel: Any = None) -> bool:
        """
        打开登录页并等待**用户本人**完成登录、验证码、身份验证。

        重要实现约束：等待期间只**被动读取**页面地址与 Cookie，绝不做任何跳转或刷新
        —— 否则会把用户正在填写的登录表单刷掉，导致“怎么都登录不上”。
        只有当页面已离开登录地址（说明用户提交成功、平台自行跳转）时，才做一次跳转核验。
        """
        from browser.actions import safe_goto  # 延迟导入避免循环

        self.start()
        assert self.page is not None
        timeout = int(timeout or self.cfg.get("LOGIN_WAIT_TIMEOUT_SEC") or 900)
        poll = max(1.0, float(self.cfg.get("LOGIN_POLL_SEC") or 3))

        # 先看当前是否已经是登录态（沿用上次会话，且不做跳转）
        if self._cookie_logged_in() and self._space_reachable(probe=False):
            print("\n[完成] 检测到已有登录态（来自持久化用户目录）")
            self.save_cookies()      # 提前返回也要落盘：否则换号/首登识别为“已有登录”却从不写 cookies.json→没记住
            return True

        safe_goto(self.page, self.cfg.get("CHAOXING_LOGIN_URL"), attempts=3)
        try:
            self.page.bring_to_front()
        except Exception:
            pass
        if on_prompt:
            try:
                on_prompt()
            except Exception:
                log.debug("on_prompt 回调异常", exc_info=True)
        print("\n" + "=" * 62)
        print("请在**已经打开的浏览器窗口**中，由你本人完成：账号密码登录 → 验证码 → 身份验证")
        print("本程序不会自动识别或绕过任何验证码 / 登录校验。")
        print("等待期间不会刷新页面；小窗已自动让位，如没看到浏览器请点任务栏的浏览器图标。")
        print(f"最长等待 {timeout} 秒。")
        print("=" * 62)
        start_ts = time.time()
        verified_at = 0.0
        while time.time() - start_ts < timeout:
            try:
                url = str(self.page.url or "")
            except PlaywrightError as exc:
                log.warning("登录状态检查失败（页面可能被关闭）：%s", exc)
                raise LoginRequired("浏览器页面已被关闭，请重新运行程序") from exc
            left_login = bool(url) and ("passport" not in url) and ("/login" not in url) \
                and (not url.startswith("about:"))
            if left_login and self._cookie_logged_in():
                # 已离开登录页 + 有真实 UID Cookie(UID 是用户号，非埋点 cookie) → 认定登录成功。
                # 绝不在此把用户 safe_goto 回登录页：那会与学习通登录后跳转互相顶、造成“无限重定向=一直转圈”。
                print("\n[完成] 检测到登录成功（已离开登录页且具备登录 Cookie），继续执行后续步骤。")
                self.save_cookies()
                if self.store:
                    self.store.log_study(None, "manual_login", "用户本人完成登录", time.time() - start_ts)
                return True
            if should_cancel is not None and should_cancel():
                print("\n[已取消] 已停止等待登录。浏览器窗口保持打开，你可以随时重新开始登录。")
                log.info("用户取消了登录等待")
                return False
            elapsed = int(time.time() - start_ts)
            if elapsed and elapsed % 30 < int(poll):
                print(f"    …已等待 {elapsed} 秒，仍在等你本人登录（页面不会被刷新）。")
            time.sleep(poll)
        print("\n[超时] 未检测到登录状态。程序不会尝试自动登录。")
        return False

    def _space_reachable(self, probe: bool = True) -> bool:
        """
        判断个人空间是否可访问。probe=False 时只看当前页面状态，绝不跳转，
        以免打断用户正在填写的登录表单。
        """
        if not self._cookie_logged_in():
            return False
        if not probe:
            try:
                url = str(self.page.url or "") if self.page else ""
            except Exception:
                return False
            return bool(url) and "passport" not in url and "/login" not in url
        from browser.actions import safe_goto

        home = self.cfg.get("CHAOXING_HOME_URL") or "https://i.chaoxing.com"
        if self.page is None or not safe_goto(self.page, f"{home.rstrip('/')}/base", attempts=1):
            return False
        try:
            final = str(self.page.url or "")
        except PlaywrightError:
            return False
        return not ("passport" in final or "/login" in final)

    # ------------------------------------------------------------ HTTP（下载课件用）
    def http_session(self) -> requests.Session:
        """
        复用当前浏览器登录态下载**自己课程里**的资料文件。
        仅用于 GET 请求读取资料，不做任何写操作。
        """
        s = requests.Session()
        s.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                "Referer": self.cfg.get("CHAOXING_HOME_URL"),
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        for name, value in self.cookie_map().items():
            s.cookies.set(name, value)
        return s

    def save_state_note(self) -> None:
        if self.store:
            self.store.set_meta("last_session_at", now_str())

    def dump_html(self, page: Page, tag: str = "page") -> Path:
        """把页面 HTML 落盘，便于改版后定位选择器（调试功能）。"""
        out_dir = Path(self.cfg.path("LOG_DIR")).parent / "data" / "dumps"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target = out_dir / f"{tag}_{stamp}.html"
        try:
            content = page.content()
        except PlaywrightError as exc:
            log.warning("导出 HTML 失败：%s", exc)
            return target
        parts = [f"<!-- url: {page.url} -->\n"]
        parts.append(content)
        for frame in page.frames[1:]:
            try:
                parts.append(f"\n\n<!-- iframe: {frame.url} -->\n{frame.content()}")
            except Exception:
                continue
        target.write_text("".join(parts), encoding="utf-8")
        print(f"[信息] 页面结构已导出：{target}")
        return target