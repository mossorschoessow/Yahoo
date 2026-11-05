"""桌面版入口：雙擊 exe 後開啟視窗，關閉視窗會縮到系統匣繼續監控。

- 背景啟動 Web 伺服器（只綁 127.0.0.1）與排程
- 用 pywebview 開原生視窗（Windows 使用系統內建的 Edge WebView2）
- 系統匣選單：開啟視窗 / 立即執行全部任務 / 開啟資料夾 / 開機自動啟動 / 結束
- 只允許同時開一個；再點一次 exe 會叫出已經在跑的視窗
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import webbrowser

from monitor import config
from monitor.i18n import tr

INSTANCE_PORT = 47821  # 單一實例鎖用的本機連接埠
log = logging.getLogger("desktop")


def setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.FileHandler(config.LOG_DIR / "monitor.log", encoding="utf-8")]
    if sys.stdout is not None:  # 打包成無主控台程式時 stdout 是 None
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def message_box(text: str, title: str | None = None) -> None:
    title = title or tr(config.APP_TITLE)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    else:
        print(text)


# --------------------------------------------------------------------------- 單一實例
def acquire_instance_lock(on_wake) -> socket.socket | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", INSTANCE_PORT))
    except OSError:
        s.close()
        try:  # 通知已經在跑的那個把視窗叫出來
            with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=2) as c:
                c.sendall(b"show")
        except OSError:
            pass
        return None
    s.listen(5)

    def serve() -> None:
        while True:
            try:
                conn, _ = s.accept()
                with conn:
                    if conn.recv(16) == b"show":
                        on_wake()
            except OSError:
                return

    threading.Thread(target=serve, daemon=True).start()
    return s


# --------------------------------------------------------------------------- 伺服器
def free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1" if config.SERVER_HOST in ("127.0.0.1", "localhost") else config.SERVER_HOST, port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError(tr("找不到可用的連接埠"))


class Server:
    def __init__(self) -> None:
        import uvicorn
        from monitor.web import app

        self.port = free_port(config.SERVER_PORT)
        cfg = uvicorn.Config(app, host=config.SERVER_HOST, port=self.port, log_config=None, access_log=False)
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> None:
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                return
            if not self.thread.is_alive():
                break
            time.sleep(0.05)
        raise RuntimeError(tr("Web 伺服器啟動失敗，詳情請看 logs/monitor.log"))

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


# --------------------------------------------------------------------------- 開機自動啟動
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_supported() -> bool:
    return sys.platform == "win32" and config.FROZEN


def autostart_enabled() -> bool:
    if not autostart_supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, config.APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, config.APP_NAME, 0, winreg.REG_SZ, f'"{sys.executable}" --minimized')
        else:
            try:
                winreg.DeleteValue(k, config.APP_NAME)
            except OSError:
                pass


# --------------------------------------------------------------------------- App
class DesktopApp:
    def __init__(self, start_minimized: bool) -> None:
        self.start_minimized = start_minimized
        self.window = None
        self.tray = None
        self.quitting = False
        self.hint_shown = False
        self.quit_event = threading.Event()
        self.server = Server()

    # --- 視窗 ---
    def show(self) -> None:
        if self.window is not None:
            self.window.show()
            self.window.restore()
        else:
            webbrowser.open(self.server.url)

    def on_closing(self):
        if self.quitting or self.tray is None:
            return True
        self.window.hide()
        if not self.hint_shown:
            self.hint_shown = True
            try:
                self.tray.notify(tr("程式仍在背景監控中，可以從右下角系統匣圖示叫出視窗或結束。"), tr(config.APP_TITLE))
            except Exception:
                pass
        return False  # 取消關閉 → 縮到系統匣

    # --- 系統匣 ---
    def start_tray(self) -> None:
        try:
            import pystray
            from monitor.icon import make_icon
        except Exception as e:
            log.warning("系統匣無法使用：%s", e)
            return

        def run_all(_icon=None, _item=None) -> None:
            from monitor import runner
            from monitor.scheduler import scheduler
            for t in runner.load_tasks():
                if t.get("enabled"):
                    scheduler.trigger(t["name"])

        def toggle_autostart(_icon, _item) -> None:
            set_autostart(not autostart_enabled())

        items = [
            pystray.MenuItem(lambda _i: tr("開啟視窗"), lambda: self.show(), default=True),
            pystray.MenuItem(lambda _i: tr("立即執行全部任務"), run_all),
            pystray.MenuItem(lambda _i: tr("開啟資料夾"), lambda: os.startfile(config.HOME_DIR) if hasattr(os, "startfile") else None),
        ]
        if autostart_supported():
            items.append(pystray.MenuItem(lambda _i: tr("開機時自動啟動"), toggle_autostart, checked=lambda _i: autostart_enabled()))
        items += [pystray.Menu.SEPARATOR, pystray.MenuItem(lambda _i: tr("結束"), lambda: self.quit())]
        self.tray = pystray.Icon(config.APP_NAME, make_icon(64), tr(config.APP_TITLE), pystray.Menu(*items))
        try:
            self.tray.run_detached()
        except Exception as e:
            log.warning("系統匣啟動失敗：%s", e)
            self.tray = None

    def quit(self) -> None:
        self.quitting = True
        self.quit_event.set()
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass

    # --- 主流程 ---
    def run(self) -> None:
        self.server.start()
        log.info("Web 伺服器：%s　資料夾：%s", self.server.url, config.HOME_DIR)
        self.start_tray()
        try:
            import webview
        except Exception as e:
            log.warning("pywebview 無法載入，改用瀏覽器：%s", e)
            webview = None

        if webview is not None:
            try:
                self.window = webview.create_window(
                    tr(config.APP_TITLE), self.server.url, width=1200, height=820, min_size=(420, 500),
                    hidden=self.start_minimized and self.tray is not None,
                )
                self.window.events.closing += self.on_closing
                webview.start(private_mode=False, storage_path=str(config.DATA_DIR / "webview"))
                # webview.start() 會一直跑到所有視窗都關閉
            except Exception as e:
                log.exception("視窗開啟失敗，改用瀏覽器：%s", e)
                self.window = None
                if not self.quitting:
                    webbrowser.open(self.server.url)
                    self.quit_event.wait()
        else:
            if not self.start_minimized:
                webbrowser.open(self.server.url)
            self.quit_event.wait()

        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.server.stop()


def main() -> None:
    setup_logging()
    app_ref: dict[str, DesktopApp] = {}
    lock = acquire_instance_lock(lambda: app_ref["app"].show() if "app" in app_ref else None)
    if lock is None:
        return  # 已經有一個在跑，已經請它把視窗叫出來
    try:
        app = DesktopApp(start_minimized="--minimized" in sys.argv)
        app_ref["app"] = app
        app.run()
    except Exception as e:
        log.exception("程式發生錯誤")
        message_box(tr("程式發生錯誤：\n{e}\n\n詳細記錄：{log}").format(e=e, log=config.LOG_DIR / "monitor.log"))
    finally:
        lock.close()


if __name__ == "__main__":
    main()
