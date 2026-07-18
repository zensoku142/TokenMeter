"""Windows Credential Manager access with legacy namespace compatibility."""

from __future__ import annotations

import ctypes
import os
from contextlib import suppress
from ctypes import wintypes

from app_identity import APP_STORAGE_NAME


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_CREDENTIALW_TYPE = 1
_advapi32 = None
if os.name == "nt":
    try:
        _advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        _advapi32.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
        ]
        _advapi32.CredReadW.restype = wintypes.BOOL
        _advapi32.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        _advapi32.CredWriteW.restype = wintypes.BOOL
        _advapi32.CredDeleteW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        _advapi32.CredDeleteW.restype = wintypes.BOOL
        _advapi32.CredFree.argtypes = [ctypes.c_void_p]
    except Exception:
        _advapi32 = None


def credential_target(key: str) -> str:
    return f"TokenMeter/{key}"


def read_credential_target(target: str) -> str:
    """Read one target without logging its secret payload."""

    if os.name != "nt" or _advapi32 is None:
        return ""
    pointer = ctypes.POINTER(_CREDENTIALW)()
    try:
        if not _advapi32.CredReadW(target, _CREDENTIALW_TYPE, 0, ctypes.byref(pointer)):
            return ""
        credential = pointer.contents
        if not credential.CredentialBlob or not credential.CredentialBlobSize:
            return ""
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return raw.decode("utf-16-le")
    except Exception:
        return ""
    finally:
        if pointer:
            with suppress(Exception):
                _advapi32.CredFree(pointer)


def read_credential(key: str) -> str:
    # 安装测试不能读取当前用户真实凭据或触发带凭据网络请求。
    if os.environ.get("TOKENMETER_E2E_DISABLE_CREDENTIALS") == "1":
        return ""
    if os.name != "nt":
        return os.environ.get(key, "")
    for prefix in ("TokenMeter", "TokenSpider", "TokenScope"):
        value = read_credential_target(f"{prefix}/{key}")
        if value:
            if prefix != "TokenMeter":
                # 复制失败不影响本次继续使用旧凭据，且旧目标会一直保留。
                with suppress(OSError):
                    write_credential(key, value)
            return value
    return ""


def write_credential(key: str, value: str) -> None:
    if os.name != "nt":
        if value:
            raise OSError("非 Windows 环境请通过同名环境变量提供凭证")
        return
    if _advapi32 is None:
        if value:
            raise OSError("Windows 凭据管理器不可用，凭据未保存")
        return
    if not value:
        if not _advapi32.CredDeleteW(credential_target(key), _CREDENTIALW_TYPE, 0):
            error = ctypes.get_last_error()
            if error != 1168:
                raise ctypes.WinError(error)
        return
    raw = value.encode("utf-16-le")
    blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    credential = _CREDENTIALW()
    credential.Type = _CREDENTIALW_TYPE
    credential.TargetName = credential_target(key)
    credential.CredentialBlobSize = len(raw)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = 2
    credential.UserName = APP_STORAGE_NAME
    if not _advapi32.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError()


__all__ = ["credential_target", "read_credential", "read_credential_target", "write_credential"]
