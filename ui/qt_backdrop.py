"""Optional Windows 11 backdrop support with a no-op safe fallback."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38

DWM_WINDOW_CORNER_PREFERENCE_ROUND = 2
DWM_SYSTEMBACKDROP_TYPE_NONE = 1
DWM_SYSTEMBACKDROP_TYPE_TRANSIENTWINDOW = 3


def _set_dwm_attribute(dwm, hwnd: int, attribute: int, value: int) -> bool:
    native_value = ctypes.c_int(value)
    result = dwm.DwmSetWindowAttribute(
        wintypes.HWND(hwnd),
        wintypes.DWORD(attribute),
        ctypes.byref(native_value),
        ctypes.sizeof(native_value),
    )
    return result == 0


def try_apply_window_backdrop(
    hwnd: int,
    theme: str,
    *,
    enabled: bool = True,
) -> bool:
    """Try to apply the documented Windows 11 transient backdrop.

    The Qt-painted glass surface remains the primary rendering path. Unsupported
    systems, layered-window incompatibilities, invalid handles and native errors
    all return ``False`` without affecting window creation or interaction.
    """

    if sys.platform != "win32" or not hwnd:
        return False
    try:
        if sys.getwindowsversion().build < 22621:
            return False
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        dwm.DwmSetWindowAttribute.argtypes = (
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long

        dark_mode = 1 if str(theme).strip().lower() == "dark" else 0
        _set_dwm_attribute(
            dwm,
            hwnd,
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            dark_mode,
        )
        _set_dwm_attribute(
            dwm,
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            DWM_WINDOW_CORNER_PREFERENCE_ROUND,
        )
        backdrop = (
            DWM_SYSTEMBACKDROP_TYPE_TRANSIENTWINDOW
            if enabled
            else DWM_SYSTEMBACKDROP_TYPE_NONE
        )
        return _set_dwm_attribute(
            dwm,
            hwnd,
            DWMWA_SYSTEMBACKDROP_TYPE,
            backdrop,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return False
