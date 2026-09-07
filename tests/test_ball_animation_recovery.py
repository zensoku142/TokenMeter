"""Visible water animation must survive repeated readings and window changes."""

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from ui.qt_ball import FloatingUsageBall


@pytest.fixture
def ball():
    app = QApplication.instance() or QApplication([])
    widget = FloatingUsageBall(88)
    widget.set_motion_provider("codex")
    widget.set_quota_state(65, "2小时后重置", "周额度")
    widget.show()
    app.processEvents()
    yield widget
    widget.close()


def test_unchanged_reading_restarts_interrupted_visible_animation(ball):
    ball._wave_timer.stop()
    ball.set_quota_state(65, "2小时后重置", "周额度")
    assert ball._wave_timer.isActive()
    assert ball._quota_remaining == 65


def test_same_reading_does_not_restart_hidden_animation(ball):
    ball.hide()
    ball.set_quota_state(65, "2小时后重置", "周额度")
    assert not ball._wave_timer.isActive()
    ball.show()
    assert ball._wave_timer.isActive()


def test_visible_water_timer_advances_without_pointer_events(ball):
    phase = ball._liquid_surface.idle_phase
    ticks = QSignalSpy(ball._wave_timer.timeout)
    QTest.qWait(250)
    assert ticks.count() >= 1
    assert ball._liquid_surface.idle_phase > phase


def test_codex_idle_surface_visibly_moves_without_pointer_input(ball):
    ball._wave_timer.stop()
    bounds = QRectF(8, 8, 104, 104)
    _, before = ball._codex_surface_paths(bounds, .65)
    for _ in range(60):
        ball._step_animation_state(1 / 60)
    _, after = ball._codex_surface_paths(bounds, .65)
    movement = max(abs(before.elementAt(i).y - after.elementAt(i).y)
                   for i in range(min(before.elementCount(), after.elementCount())))
    # Default-size waves should move by visible pixels, not merely subpixel shimmer.
    assert movement * 88 / 120 > 1.5
    assert ball._quota_remaining == 65


@pytest.mark.parametrize("remaining", [None, 0])
def test_unknown_or_empty_reading_keeps_animation_stopped(ball, remaining):
    ball.set_quota_state(remaining, "", "周额度")
    ball.set_quota_state(remaining, "", "周额度")
    assert not ball._wave_timer.isActive()
