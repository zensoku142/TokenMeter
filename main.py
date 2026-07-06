"""TokenSpider — real-time LLM API usage monitor (floating desktop widget)."""

import ctypes
import sys
from ctypes import wintypes

import config_manager
from PySide6.QtWidgets import QApplication

from ui.qt_theme import APP_STYLE, app_icon
from ui.qt_tray import SystemTray
from ui.qt_widget import FloatingWidget

__version__ = "1.1.2"

ERROR_ALREADY_EXISTS = 183
SINGLE_INSTANCE_MUTEX = "Local\\TokenSpider.SingleInstance"


def _acquire_single_instance():
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    # 句柄必须存活到主循环退出，Windows 才会持续阻止其他实例启动。
    return handle


def _release_single_instance(handle) -> None:
    if sys.platform == "win32" and handle:
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(handle)


def _handle_exception(exc_type, exc, traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, traceback)
        return
    config_manager.logger().critical(
        "Unhandled application error", exc_info=(exc_type, exc, traceback)
    )


class App:
    def __init__(self):
        self.qt_app = QApplication.instance() or QApplication(sys.argv)
        self.qt_app.setQuitOnLastWindowClosed(False)
        self.qt_app.setApplicationName("TokenSpider")
        self.qt_app.setWindowIcon(app_icon(64))
        self.qt_app.setStyleSheet(APP_STYLE)
        self.widget = FloatingWidget(tray_icon=None)
        self.tray = SystemTray(self)
        self.widget.tray = self.tray

    def run(self):
        sys.excepthook = _handle_exception
        config_manager.logger().info("TokenSpider %s started", __version__)
        self.tray.run()
        try:
            return self.qt_app.exec()
        finally:
            self.tray.stop()
            config_manager.logger().info("TokenSpider stopped")


if __name__ == "__main__":
    instance_handle = _acquire_single_instance()
    if instance_handle is None:
        ctypes.windll.user32.MessageBoxW(
            None, "TokenSpider 已在运行。", "TokenSpider", 0x40
        )
    else:
        try:
            App().run()
        finally:
            _release_single_instance(instance_handle)
