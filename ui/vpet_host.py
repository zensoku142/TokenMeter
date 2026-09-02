"""Optional VPet child process; only display data crosses the local pipes."""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from core import pet_characters
from core.pet_extension import installed_executable
from ui.formatting import format_codex_reset_time, format_money, format_reset_countdown

if TYPE_CHECKING:
    from data.store import TokenData


def host_executable() -> Path:
    installed = installed_executable()
    if installed is not None:
        return installed
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "pet" / "TokenMeter.Pet.exe"
    build = Path(__file__).resolve().parents[1] / "build"
    try:
        active = json.loads((build / "vpet-active.json").read_text(encoding="utf-8"))
        selected = (build / active["directory"]).resolve()
        executable = selected / "TokenMeter.Pet.exe"
        # 只允许构建目录的直接子目录，且不覆盖安装版的固定宿主位置。
        if selected.parent == build.resolve() and executable.is_file():
            return executable
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return build / "vpet" / "TokenMeter.Pet.exe"


def usage_message(
    data: TokenData, refreshing: bool, provider_id: str, pricing_peak: bool | None = None
) -> dict:
    provider = data.per_provider[0].provider_id if data.per_provider else provider_id
    loading = refreshing and data.last_success_at is None
    status = "正在刷新用量" if refreshing else ""
    warning = data.status not in {"ok", "partial", "loading"}
    if data.quota_windows:
        quota = data.quota_windows[0]
        used = quota.used_percent
        remaining = None if loading or not math.isfinite(used) else max(0, min(100, 100 - used))
        primary = "--" if remaining is None else f"剩余 {remaining:.0f}%"
        secondary = (
            format_codex_reset_time(quota.resets_at, compact=True)
            if provider == "codex"
            else format_reset_countdown(quota.resets_at)
        )
        label = f"{provider} · {quota.title}"
        if not refreshing and remaining is not None and remaining <= 10:
            status = "剩余额度不足，请留意用量。"
            warning = True
    elif provider in {"codex", "cursor"}:
        label, primary, secondary = provider, "--", "额度暂不可用"
    else:
        label = provider
        primary = "余额 " + ("--" if loading else format_money(data.balance_cny, data.currency))
        secondary = "今日使用 " + (
            "--" if loading else format_money(data.today_cost_cny, data.currency)
        )
    if data.status not in {"ok", "partial", "loading"} and not refreshing:
        status = (
            "用量更新失败，显示上次数据。"
            if data.last_success_at
            else "用量暂不可用，请检查账户连接。"
        )
    # 显式列出允许发送的展示字段；绝不序列化 TokenData、配置或服务商的凭据对象。
    message = {
        "type": "usage",
        "provider": label,
        "primary": primary,
        "secondary": secondary,
        "status": status,
        "warning": warning,
    }
    # 可选展示字段兼容旧宿主；仅 DeepSeek 余额使用分时描边，不把峰谷状态带到其它账户。
    if provider == "deepseek" and not data.quota_windows and pricing_peak is not None:
        message["pricing_peak"] = pricing_peak
    return message


class VPetHost(QObject):
    ready = Signal()
    failed = Signal(str)
    action_requested = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.readyReadStandardError.connect(self._discard_stderr)
        self.process.errorOccurred.connect(self._process_error)
        self.process.finished.connect(self._finished)
        self._startup_timer = QTimer(self)
        self._startup_timer.setSingleShot(True)
        self._startup_timer.setInterval(120_000)
        self._startup_timer.timeout.connect(lambda: self._fail("桌宠加载超时，已返回悬浮球。"))
        self.active = False
        self.animations = 0
        self.visible = True
        self._stopping = False
        self._reported_failure = False
        self._buffer = bytearray()
        self._latest_usage: dict | None = None
        self._resources_directory: Path | None = None

    def start(self, data_directory: Path) -> None:
        resources = pet_characters.selected_resources_directory()
        resources = resources.resolve() if resources is not None else None
        if self.process.state() != QProcess.ProcessState.NotRunning:
            if resources == self._resources_directory:
                return
            # 角色切换只重启本实例创建的宿主；窗口布局和其它桌宠偏好仍在原数据目录。
            self.stop()
        self._reported_failure = self._stopping = False
        self._buffer.clear()
        executable = host_executable()
        if not executable.is_file():
            self._fail("未安装桌宠扩展包，请在“设置 → 桌宠”下载；当前继续使用悬浮球。")
            return
        data_directory.mkdir(parents=True, exist_ok=True)
        self.process.setProgram(str(executable))
        arguments = ["--data-dir", str(data_directory), "--parent-pid", str(os.getpid())]
        if resources is not None:
            arguments.extend(["--resources-dir", str(resources)])
        self.process.setArguments(arguments)
        self.process.setWorkingDirectory(str(executable.parent))
        self._resources_directory = resources
        self._startup_timer.start()
        self.process.start()

    def update_usage(self, message: dict) -> None:
        self._latest_usage = message
        if self.active:
            self._send(message)

    def set_visible(self, visible: bool) -> None:
        self.visible = visible
        if self.active:
            self._send({"type": "visibility", "visible": visible})

    def _send(self, message: dict) -> None:
        if self.process.state() == QProcess.ProcessState.Running:
            self.process.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))

    def _read_output(self) -> None:
        self._consume_output(bytes(self.process.readAllStandardOutput()))

    def _consume_output(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)
        # 子进程错误输出或无换行数据不能无限占用主程序内存。
        if len(self._buffer) > 65536:
            self._fail("桌宠通信异常，已返回悬浮球。")
            self._buffer.clear()
            return
        while b"\n" in self._buffer:
            raw, _, tail = self._buffer.partition(b"\n")
            self._buffer = bytearray(tail)
            try:
                message = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(message, dict) or self._stopping or self._reported_failure:
                continue
            event = message.get("event")
            if event == "ready" and not self.active:
                self._startup_timer.stop()
                self.active = True
                count = message.get("animations", 0)
                self.animations = count if isinstance(count, int) else 0
                if self._latest_usage is not None:
                    self._send(self._latest_usage)
                self._send({"type": "visibility", "visible": self.visible})
                self.ready.emit()
            elif event == "error":
                self._fail("桌宠运行异常，已返回悬浮球。")
            elif self.active and event in {"open_panel", "open_settings", "disable_pet", "quit"}:
                # 只接受固定的本地 UI 动作，子进程不能要求主程序执行命令或访问任意地址。
                self.action_requested.emit(event)

    def _discard_stderr(self) -> None:
        self.process.readAllStandardError()

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if not self._stopping:
            self._fail("桌宠无法启动，请检查资源和 .NET Desktop Runtime。")

    def _finished(self, _code: int, _status: QProcess.ExitStatus) -> None:
        self.active = False
        self._startup_timer.stop()
        if not self._stopping:
            self._fail("桌宠已意外退出，已返回悬浮球。")

    def _fail(self, message: str) -> None:
        if self._reported_failure:
            return
        self._reported_failure = True
        self.stop()
        self.failed.emit(message)

    def stop(self) -> None:
        self._stopping = True
        self.active = False
        self._startup_timer.stop()
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        # 先请求保存并退出，超时才终止本实例创建的子进程；不查杀其它 VPet 进程。
        self._send({"type": "shutdown"})
        self.process.closeWriteChannel()
        if not self.process.waitForFinished(2000):
            self.process.kill()
            self.process.waitForFinished(1000)
