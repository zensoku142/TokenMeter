import ctypes
import os
import sys
import threading
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from api import cc_switch_windows

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows window APIs required")


def test_process_check_finds_the_current_executable():
    assert cc_switch_windows.is_running(sys.executable)
    assert not cc_switch_windows.is_running("tokenmeter-nonexistent-process-492817.exe")


@pytest.mark.parametrize("show_during_startup", [True, False])
def test_startup_window_is_hidden_and_can_be_opened_again(qtbot, show_during_startup):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    window_proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM)
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t

    @window_proc_type
    def window_proc(hwnd, message, wparam, lparam):
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    class WindowClass(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("proc", window_proc_type),
                    ("cls_extra", ctypes.c_int), ("wnd_extra", ctypes.c_int),
                    ("instance", wintypes.HINSTANCE), ("icon", wintypes.HICON),
                    ("cursor", wintypes.HANDLE), ("background", wintypes.HBRUSH),
                    ("menu", wintypes.LPCWSTR), ("name", wintypes.LPCWSTR)]

    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    instance = kernel32.GetModuleHandleW(None)
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
    wc = WindowClass(0, window_proc, 0, 0, instance, None, None, None, None, "Tauri Window")
    assert user32.RegisterClassW(ctypes.byref(wc))
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                      wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, wintypes.HWND, wintypes.HANDLE,
                                      wintypes.HINSTANCE, ctypes.c_void_p]
    user32.CreateWindowExW.restype = wintypes.HWND
    # 测试窗口放在屏幕外且不接收焦点，验证过程中不会打扰用户。
    hwnd = user32.CreateWindowExW(0x08000080, "Tauri Window", "CC test", 0x00CF0000,
                                  -32000, -32000, 400, 300, None, None, instance, None)
    assert hwnd
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetLayeredWindowAttributes.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
                                                  ctypes.POINTER(wintypes.BYTE), ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    original_style = user32.GetWindowLongW(hwnd, -20)
    process = SimpleNamespace(pid=os.getpid(), poll=lambda: None)
    worker = threading.Thread(target=cc_switch_windows.hide_startup_window, args=(process, 1), daemon=True)
    worker.start()

    def is_transparent():
        alpha, flags = wintypes.BYTE(), wintypes.DWORD()
        return (user32.GetLayeredWindowAttributes(hwnd, None, ctypes.byref(alpha), ctypes.byref(flags))
                and flags.value & 0x2 and alpha.value == 0)

    try:
        qtbot.waitUntil(lambda: bool(is_transparent()), timeout=2000)
        if show_during_startup:
            user32.ShowWindow(hwnd, 5)
        qtbot.waitUntil(lambda: not worker.is_alive(), timeout=2000)
        assert not user32.IsWindowVisible(hwnd)
        assert user32.GetWindowLongW(hwnd, -20) == original_style

        user32.ShowWindow(hwnd, 5)
        assert user32.IsWindowVisible(hwnd)
    finally:
        user32.DestroyWindow(hwnd)
        user32.UnregisterClassW("Tauri Window", instance)
