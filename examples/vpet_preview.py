"""Preview the real VPet integration without reading accounts or changing user settings."""

from __future__ import annotations

import os
import sys
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# 预览状态全部留在项目 build 目录，不调用主程序的数据迁移或凭据初始化。
os.environ["APPDATA"] = str(ROOT / "build" / "vpet-preview-state")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from api.providers.base import QuotaWindow
from config import runtime as config
from config.defaults import DEFAULT_CONFIG
from data.store import PerProviderData, TokenData
from ui.qt_theme import configure_theme
from ui.qt_update import AppUpdateController
from ui.qt_widget import FloatingWidget


def main(*, preview_character: str | None = None) -> int:
    app = QApplication([])
    app.setApplicationName("TokenMeter VPet Preview")
    configure_theme(app, "dark")
    values = {
        **DEFAULT_CONFIG,
        "VPET_ENABLED": True,
        "ACTIVE_PROVIDER": "codex",
        "UPDATE_AUTO_CHECK_ENABLED": False,
        "PANEL_AUTO_COLLAPSE_ON_DEACTIVATE": False,
    }
    data = TokenData(
        status="ok",
        last_success_at=datetime.now(),
        per_provider=[PerProviderData("codex", "Codex（演示数据）", status="ok")],
        quota_windows=[QuotaWindow("weekly", "周额度（演示数据）", 35)],
    )

    def save(changes):
        values.update(config.validate_config({**values, **changes}))
        return values.copy()

    def refresh(widget, *args, **kwargs):
        widget._data = data
        widget._apply_update()

    with ExitStack() as stack:
        for name, replacement in (
            ("get", lambda key, default=None: values.get(key, default)),
            ("all_config", lambda: values.copy()),
            ("load_config", lambda: values.copy()),
            ("save_config", save),
            ("load_widget_size", lambda: 88),
            ("load_widget_position", lambda: (1000, 500)),
        ):
            stack.enter_context(patch.object(config, name, replacement))
        stack.enter_context(patch.object(TokenData, "persisted_snapshot", return_value=data))
        stack.enter_context(patch.object(FloatingWidget, "refresh", refresh))
        stack.enter_context(patch.object(AppUpdateController, "schedule_startup_check"))
        stack.enter_context(patch("ui.qt_settings.sync_autostart"))
        widget = FloatingWidget()

        class Controls(QWidget):
            def closeEvent(self, event):
                widget.close()
                event.accept()

        controls = Controls()
        controls.setWindowTitle("小龙娘 · 原框架预览（演示数据）" if preview_character == "bailong"
                               else "VPet 精简版 · 独立试用（演示数据）")
        layout = QVBoxLayout(controls)
        layout.addWidget(
            QLabel(
                "右键打开操作菜单，无底部工具栏\n轻触头/身体互动；按住移动即可拖动\n保留自主走动，不含养成；额度是演示数据"
            )
        )
        status = QLabel("正在加载默认动画…")
        layout.addWidget(status)
        widget._vpet.ready.connect(
            lambda: status.setText(f"已连接：{widget._vpet.animations} 段动画")
        )
        widget._vpet.failed.connect(status.setText)

        def change(state):
            data.status = "error" if state == "error" else "ok"
            data.quota_windows = (
                []
                if state == "error"
                else [QuotaWindow("weekly", "周额度（演示数据）", 95 if state == "low" else 35)]
            )
            widget._refreshing = state == "refresh"
            refresh(widget)

        for label, state in (
            ("正常额度 65%", "idle"),
            ("刷新中", "refresh"),
            ("低额度 5%", "low"),
            ("数据异常", "error"),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=state: change(value))
            layout.addWidget(button)
        for label, action in (
            ("打开 TokenMeter 原面板", lambda: widget._on_vpet_action("open_panel")),
            ("显示 / 隐藏桌宠", widget.set_visible_from_tray),
            ("打开设置", widget.open_settings),
            ("关闭试用", controls.close),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, callback=action: callback())
            layout.addWidget(button)
        controls.show()
        if "--verify" in sys.argv:
            # 测试生产 QProcess 启动、真实管道、主面板切换和完整关闭，不使用替身桌宠。
            ticks = 0
            timer = QTimer(controls)
            timer.setInterval(200)

            def verify():
                nonlocal ticks
                ticks += 1
                if ticks > 600:
                    print("VPET_VERIFY_TIMEOUT", flush=True)
                    app.exit(1)
                elif widget._vpet.active:
                    timer.stop()
                    change("low")
                    widget._on_vpet_action("open_panel")
                    assert widget._expanded
                    widget.collapse_panel()
                    assert not widget.isVisible()
                    widget.set_visible_from_tray()
                    assert not widget._vpet.visible
                    widget.set_visible_from_tray()
                    assert widget._vpet.visible
                    widget._vpet.stop()
                    print(
                        f"VPET_VERIFY_OK animations={widget._vpet.animations} exit={widget._vpet.process.exitCode()}",
                        flush=True,
                    )
                    widget.close()

            timer.timeout.connect(verify)
            timer.start()
        try:
            return app.exec()
        finally:
            widget._closed = True
            widget._vpet.stop()
            widget.hide()


if __name__ == "__main__":
    raise SystemExit(main())
