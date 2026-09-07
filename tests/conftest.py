from pathlib import Path

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import ownedByPython


@pytest.fixture(autouse=True)
def isolate_native_credentials(monkeypatch, tmp_path):
    from config import credentials
    from config import account_profiles
    from data import history

    # 项目直接调用 Win32 凭据 API，不受 PYTHON_KEYRING_BACKEND 控制；测试默认断开真实后端。
    # 凭据单元测试可显式注入假的 Win32 实现，普通测试遗漏 mock 时写入应失败而非改动用户密钥。
    monkeypatch.setattr(credentials, "_advapi32", None)
    # 自动统计会写入可选本地快照；每个测试必须与用户的真实用量数据库隔离。
    monkeypatch.setattr(history, "DB_PATH", tmp_path / "usage.db")
    monkeypatch.setattr(account_profiles, "_path", lambda: tmp_path / "account-profiles.json")
    # CLI 自动发现也只能看到临时目录；各账号用例再显式注入自己的登录文件。
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))


@pytest.fixture(autouse=True)
def cleanup_qt_widgets(isolate_native_credentials):
    app = QApplication.instance()
    existing = set(app.topLevelWidgets()) if app is not None else set()
    yield
    from PySide6.QtCore import QThreadPool

    # 等待本地扫描释放临时 DB，再撤销 fixture，避免回调写入恢复后的真实数据路径。
    QThreadPool.globalInstance().waitForDone()
    app = QApplication.instance()
    if app is None:
        return

    # close() 通常只隐藏窗口；逐例销毁新建窗口，避免主题/语言刷新反复遍历历史控件。
    # 此时测试的 mock 已撤销，不再调用可能保存配置的 close()；关闭行为仍由用例验证。
    for widget in app.topLevelWidgets():
        # Qt 自建的桌面等内部窗口也出现在列表中，不能由测试删除；子控件随所属窗口释放。
        if widget not in existing and ownedByPython(widget):
            widget.deleteLater()
    # pytest 没有运行 app.exec()，必须显式处理延迟删除，才能在下个用例前释放子控件和定时器。
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
