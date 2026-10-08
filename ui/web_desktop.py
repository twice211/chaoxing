"""React desktop entry point backed by the existing Python services."""
from __future__ import annotations

import os
import queue
from pathlib import Path

from config import BASE_DIR, Config
from ui.web_bridge import BridgeApi, DesktopScheduler
from utils.logger import get_logger

log = get_logger("ui.web_desktop")


def _show_error(message: str) -> None:
    log.error(message)
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "学习通助手 · 启动失败", 0x10)
    else:
        print(message)


def run_desktop(cfg: Config, *, bundle_path: Path | None = None) -> int:
    bundle = bundle_path or BASE_DIR / "frontend" / "dist" / "index.html"
    if not bundle.is_file():
        _show_error("未找到界面文件。请在 frontend 目录运行 npm ci，再运行 npm run build。")
        return 2
    try:
        import webview
    except ImportError:
        _show_error("缺少桌面界面依赖。请运行 python -m pip install -r requirements.txt。")
        return 2

    scheduler = DesktopScheduler(cfg, queue.Queue())
    api = BridgeApi(cfg, scheduler=scheduler)
    try:
        window = webview.create_window(
            "学习通助手", url=str(bundle.resolve()),
            width=1240, height=820, min_size=(960, 640),
            background_color="#F4F6FB", text_select=True,
        )
        # Expose individual functions, so raw JS dispatch cannot traverse the API object.
        window.expose(api.bootstrap, api.poll, api.command)
        window.events.closing += scheduler.shutdown
        scheduler.start()
        # The entry point's directory is the static root; personal data is outside it.
        webview.start(http_server=True, private_mode=True, debug=False,
                      gui="edgechromium" if os.name == "nt" else None,
                      icon=str(BASE_DIR / "assets" / "app.ico"))
        return 0
    except Exception:
        log.exception("桌面界面启动失败")
        _show_error("界面启动失败，请查看 logs 中的记录。Windows 请确认已安装 Microsoft Edge WebView2 Runtime。\n"
                    "也可以运行 python main.py --legacy-gui 使用原界面。")
        return 2
    finally:
        scheduler.shutdown()
        if scheduler.is_alive():
            scheduler.join(timeout=5)
            if scheduler.is_alive():
                log.warning("任务仍在结束当前操作，已请求取消")
            else:
                api._close()
        else:
            api._close()
