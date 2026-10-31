"""Windows-only helpers for starting the CC Switch bridge in the background."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from pathlib import Path


def is_running(executable: str) -> bool:
    if os.name != "nt":
        return False

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
            ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
            ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
            ("parent_pid", wintypes.DWORD), ("priority", ctypes.c_long),
            ("flags", wintypes.DWORD), ("exe", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snapshot == ctypes.c_void_p(-1).value:
        # 无法检查进程时不要再次启动单实例，否则会把用户的 CC 窗口唤到前台。
        return True
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        if not found:
            return ctypes.get_last_error() != 18  # ERROR_NO_MORE_FILES
        name = Path(executable).name.casefold()
        while found:
            if entry.exe.casefold() == name:
                return True
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        return ctypes.get_last_error() != 18  # ERROR_NO_MORE_FILES
    finally:
        kernel32.CloseHandle(snapshot)


def hide_startup_window(process, timeout: float = 30) -> None:
    if os.name != "nt":
        return

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

    hwnd = None
    original_style = None
    gwl_exstyle, lwa_alpha, sw_hide = -20, 0x2, 0
    layered, no_activate, tool_window, app_window = 0x80000, 0x08000000, 0x80, 0x40000

    @callback_type
    def find_window(candidate, _):
        nonlocal hwnd
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(candidate, ctypes.byref(pid))
        if pid.value == process.pid:
            name = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(candidate, name, len(name))
            if name.value == "Tauri Window":
                hwnd = candidate
                return False
        return True

    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and process.poll() is None:
            if hwnd is None:
                user32.EnumWindows(find_window, 0)
                if hwnd is not None and not user32.IsWindowVisible(hwnd):
                    style = user32.GetWindowLongW(hwnd, gwl_exstyle)
                    if not style & layered:
                        # CC 加载页面后会主动 show；先暂时透明且禁止激活，避免主窗口闪现。
                        original_style = style
                        ctypes.set_last_error(0)
                        previous = user32.SetWindowLongW(
                            hwnd, gwl_exstyle, (style | layered | no_activate | tool_window) & ~app_window,
                        )
                        if not previous and ctypes.get_last_error():
                            original_style = None
                        elif not user32.SetLayeredWindowAttributes(hwnd, 0, 0, lwa_alpha):
                            user32.SetWindowLongW(hwnd, gwl_exstyle, style)
                            original_style = None
            if hwnd is not None and user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, sw_hide)
                break
            time.sleep(0.01)
    finally:
        if hwnd is not None and original_style is not None and user32.IsWindow(hwnd):
            if user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, sw_hide)
            # 仅还原本次更改的样式位，保留 CC 启动期间自行更新的其他窗口样式。
            mask = layered | no_activate | tool_window | app_window
            style = user32.GetWindowLongW(hwnd, gwl_exstyle)
            user32.SetWindowLongW(hwnd, gwl_exstyle, (style & ~mask) | (original_style & mask))
