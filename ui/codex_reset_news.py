"""One shared background poll and restart-safe notification deduplication."""

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from api.codex_resets import fetch_status, reset_news
from config import runtime as config_manager
from config import state


class _Signals(QObject):
    finished = Signal(object)


class _NewsTask(QRunnable):
    def __init__(self):
        super().__init__()
        self.signals = _Signals()

    def run(self):
        try:
            payload = fetch_status()
        except Exception:
            payload = None
        self.signals.finished.emit(payload)


class CodexResetNews(QObject):
    updated = Signal(object)
    notification = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.payload = None
        self._enabled = False
        self._fetching = False
        self._path = config_manager.CONFIG_DIR / "codex-reset-news.json"
        saved = state.load_dict(self._path)
        seen = saved.get("seen")
        self._seen = [key for key in seen if isinstance(key, str)][-64:] if isinstance(seen, list) else []
        self._initialized = bool(saved.get("initialized"))
        self._timer = QTimer(self)
        self._timer.setInterval(300_000)
        self._timer.timeout.connect(self.refresh)
        # 倒计时按分钟更新；不为刷新文字增加公开接口请求。
        self._clock = QTimer(self)
        self._clock.setInterval(60_000)
        self._clock.timeout.connect(lambda: self.updated.emit(self.payload))

    def set_enabled(self, enabled):
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            self._timer.start()
            self._clock.start()
            self.refresh()
        else:
            self._timer.stop()
            self._clock.stop()
            self.updated.emit(None)

    def refresh(self):
        if not self._enabled or self._fetching:
            return
        self._fetching = True
        task = _NewsTask()
        task.signals.finished.connect(self._finished)
        QThreadPool.globalInstance().start(task)

    def _finished(self, payload):
        self._fetching = False
        if not self._enabled:
            return
        if payload is not None:
            self.payload = payload
        elif self.payload is not None:
            # 请求失败只保留有来源的旧记录，并停止把它当作有效预告通知。
            self.payload = dict(self.payload, stale=True)
        self.updated.emit(self.payload)
        if self.payload is None:
            return
        try:
            news = reset_news(self.payload)
        except (TypeError, ValueError, AttributeError):
            return
        if not news["fresh"]:
            return
        notifications = []
        for key in (news["notice_key"], news["record_key"]):
            if key and key not in self._seen:
                # 首次读取将历史作为基线；新的有效预告可立即提醒。
                if self._initialized or key.startswith("notice:"):
                    notifications.append("Codex 有新的公开重置预告" if key.startswith("notice:") else "Codex 有新的公开重置记录")
                self._seen.append(key)
        self._initialized = True
        self._seen = self._seen[-64:]
        try:
            state.merge_dict(self._path, {"seen": self._seen, "initialized": True})
        except OSError:
            config_manager.logger().warning("Reset news notification state could not be saved")
        for message in notifications:
            self.notification.emit(message)
