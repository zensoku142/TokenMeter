"""TokenMeter real-time LLM API usage monitor."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

import config_manager
from app_identity import APP_DISPLAY_NAME, APP_VERSION, SINGLE_INSTANCE_MUTEX

__version__ = APP_VERSION

ERROR_ALREADY_EXISTS = 183


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
    # Keep the legacy mutex name so upgraded builds still prevent duplicate
    # instances from older TokenSpider binaries using the same user profile.
    return handle


def _release_single_instance(handle) -> None:
    if sys.platform == "win32" and handle:
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(handle)


def _handle_exception(exc_type, exc, traceback) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, traceback)
        return
    config_manager.logger().critical(
        "Unhandled application error", exc_info=(exc_type, exc, traceback)
    )


class App:
    def __init__(self):
        from PySide6.QtWidgets import QApplication

        from ui.qt_theme import app_icon, configure_theme
        from ui.qt_tray import SystemTray
        from ui.qt_widget import FloatingWidget

        instance = QApplication.instance()
        self.qt_app = instance if isinstance(instance, QApplication) else QApplication(sys.argv)
        self.qt_app.setQuitOnLastWindowClosed(False)
        self.qt_app.setApplicationName(APP_DISPLAY_NAME)
        configure_theme(self.qt_app, config_manager.get("UI_THEME", "dark"))
        self.qt_app.setWindowIcon(app_icon(64))
        self.widget = FloatingWidget(tray_icon=None)
        self.tray = SystemTray(self)
        self.widget.tray = self.tray

    def run(self):
        sys.excepthook = _handle_exception
        config_manager.logger().info("%s %s started", APP_DISPLAY_NAME, __version__)
        self.tray.run()
        try:
            return self.qt_app.exec()
        finally:
            self.tray.stop()
            config_manager.logger().info("%s stopped", APP_DISPLAY_NAME)


def main() -> int:
    instance_handle = _acquire_single_instance()
    if instance_handle is None:
        ctypes.windll.user32.MessageBoxW(None, f"{APP_DISPLAY_NAME} 已在运行。", APP_DISPLAY_NAME, 0x40)
        return 0
    try:
        config_manager.initialize()
        # 更新清理依赖已解析的数据目录，但必须先于 QApplication 执行。
        from app_update import cleanup_pending_update

        cleanup_pending_update()
        return int(App().run())
    finally:
        _release_single_instance(instance_handle)


if __name__ == "__main__":
    raise SystemExit(main())
