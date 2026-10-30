"""Windows Credential Manager access with legacy namespace compatibility."""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
from contextlib import suppress
from ctypes import wintypes
from uuid import uuid4

from core.identity import APP_STORAGE_NAME


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
_CHUNK_PREFIX = "TokenMeter:chunks-v1:"
_CHUNK_CHAR_LIMIT = 640
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


def _read_raw_target(target: str) -> str:
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


def _chunk_metadata(target: str, value: str) -> tuple[list[str], str] | None:
    # 限制损坏清单触发的原生读取次数；写入端使用相同的分段数量上限。
    match = re.fullmatch(re.escape(_CHUNK_PREFIX) + r"([0-9a-f]{32}):([1-9][0-9]{0,3}):([0-9a-f]{64})", value)
    if match is None or int(match[2]) > 1024:
        return None
    return [f"{target}/chunks/{match[1]}/{index}" for index in range(int(match[2]))], match[3]


def read_credential_target(target: str) -> str:
    value = _read_raw_target(target)
    if not value.startswith(_CHUNK_PREFIX):
        return value
    metadata = _chunk_metadata(target, value)
    if metadata is None:
        return ""
    targets, digest = metadata
    parts = [_read_raw_target(part) for part in targets]
    # 分段缺失或损坏时不能把截断凭据交给接口，也不能把存储清单当成真实密钥。
    if not all(parts):
        return ""
    value = "".join(parts)
    return value if hashlib.sha256(value.encode("utf-16-le")).hexdigest() == digest else ""


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
        # 包括清空操作也必须报告失败，不能把“无法访问后端”伪装成已删除。
        raise OSError("Windows 凭据管理器不可用，凭据未保存或清除")
    if not value:
        # 清除必须覆盖兼容读取来源，否则下一次加载会把旧凭据重新迁回。
        for prefix in ("TokenSpider", "TokenScope", "TokenMeter"):
            target = f"{prefix}/{key}"
            metadata = _chunk_metadata(target, _read_raw_target(target))
            for part in metadata[0] if metadata else []:
                _delete_target(part)
            _delete_target(target)
        return
    target = credential_target(key)
    previous = _read_raw_target(target)
    old_parts = _chunk_metadata(target, previous)
    # 自动保存会提交所有平台凭据；未变化的大 Cookie 无需重复创建和清理多个记录。
    if read_credential_target(target) == value:
        return
    if len(value.encode("utf-16-le")) <= 2560 and not value.startswith(_CHUNK_PREFIX):
        _write_raw_target(target, value)
    else:
        # Windows 单条凭据最多 2560 字节；按字符切分，非 BMP 字符也不会切断 UTF-16 编码。
        parts = [value[index:index + _CHUNK_CHAR_LIMIT] for index in range(0, len(value), _CHUNK_CHAR_LIMIT)]
        if len(parts) > 1024:
            raise OSError("凭据过大，无法保存")
        generation = uuid4().hex
        digest = hashlib.sha256(value.encode("utf-16-le")).hexdigest()
        manifest = f"{_CHUNK_PREFIX}{generation}:{len(parts)}:{digest}"
        metadata = _chunk_metadata(target, manifest)
        assert metadata is not None
        created = []
        try:
            for part_target, part in zip(metadata[0], parts):
                _write_raw_target(part_target, part)
                created.append(part_target)
            # 新分段全部写入后才切换主记录；中途失败时并发读取仍能得到完整的旧凭据。
            _write_raw_target(target, manifest)
        except Exception:
            for part_target in created:
                with suppress(OSError):
                    _delete_target(part_target)
            raise
    for part_target in old_parts[0] if old_parts else []:
        with suppress(OSError):
            _delete_target(part_target)


def _delete_target(target: str) -> None:
    if _advapi32 is None:
        raise OSError("Windows 凭据管理器不可用")
    if not _advapi32.CredDeleteW(target, _CREDENTIALW_TYPE, 0):
        error = ctypes.get_last_error()
        if error != 1168:
            raise ctypes.WinError(error)


def _write_raw_target(target: str, value: str) -> None:
    if _advapi32 is None:
        raise OSError("Windows 凭据管理器不可用")
    raw = value.encode("utf-16-le")
    blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    credential = _CREDENTIALW()
    credential.Type = _CREDENTIALW_TYPE
    credential.TargetName = target
    credential.CredentialBlobSize = len(raw)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = 2
    credential.UserName = APP_STORAGE_NAME
    if not _advapi32.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError()


__all__ = ["credential_target", "read_credential", "read_credential_target", "write_credential"]
