"""Custom-painted floating usage ball."""

from __future__ import annotations

import logging
import math

from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QTransform,
)
from PySide6.QtWidgets import QWidget

from ui.qt_theme import DARK_THEME, LIGHT_THEME, current_theme, theme_controller

logger = logging.getLogger(__name__)

DESIGN_SIZE = 120

# 液体手感参数集中在这里，避免调试波动时在事件和绘制代码间追逐魔法数字。
LIQUID_NODE_COUNT = 14
SPRING_STRENGTH = 48.0
DAMPING = 6.4
WAVE_SPREAD = 44.0
MOUSE_IMPULSE_STRENGTH = 0.075
MOUSE_SPLIT_MIN_WIDTH = 1.8
MOUSE_SPLIT_MAX_WIDTH = 3.5
DRAG_INERTIA_STRENGTH = 0.0045
MAX_WAVE_HEIGHT = 0.16
SETTLE_HEIGHT_THRESHOLD = 0.0012
SETTLE_VELOCITY_THRESHOLD = 0.006
POINTER_SAMPLE_INTERVAL_MS = 8
POINTER_VELOCITY_FILTER = 0.3
ACTIVE_FRAME_INTERVAL_MS = 16
IDLE_FRAME_INTERVAL_MS = 40
IDLE_WAVE_PRIMARY_AMPLITUDE = 1.85
IDLE_WAVE_SECONDARY_AMPLITUDE = 0.55
IDLE_WAVE_TERTIARY_AMPLITUDE = 0.22
IDLE_WEIGHT_ACTIVE = 0.28
HIGH_LEVEL_FLOW_START = 0.80
INTERNAL_FLOW_DECAY = 1.8
INTERNAL_FLOW_VELOCITY_DECAY = 3.2
HIGH_LEVEL_SURFACE_START = 0.90
FULL_LEVEL_OBSERVATION_BAND_PX = 7.0

# Codex 专用的轻量真实液体参数。屏幕位移会按球直径归一化，避免滚轮缩放改变手感。
REALISTIC_IDLE_SPEED = 0.55
REALISTIC_IDLE_SCALE = 0.45
REALISTIC_GRAVITY = 26.0
REALISTIC_INERTIA_BASE_SCALE = 0.55
REALISTIC_INERTIA_MAX_SCALE = 2.2
REALISTIC_INERTIA_GAIN_START = 15.0
REALISTIC_INERTIA_GAIN_END = 45.0
REALISTIC_SLOSH_FREQUENCY = 8.2
REALISTIC_SLOSH_DAMPING_RATIO = 0.28
REALISTIC_NODE_DAMPING = 2.4
REALISTIC_MAX_WAVE_HEIGHT = 0.24
REALISTIC_FULL_RESPONSE_RATIO = 0.10
REALISTIC_ACCELERATION_DECAY = 9.0
REALISTIC_MAX_BULK_OFFSET = 0.14
REALISTIC_BULK_STRENGTH = 72.0
REALISTIC_BULK_DAMPING = 4.8
REALISTIC_GRAVITY_RESPONSE = 16.0
REALISTIC_BODY_LIFT_SCALE = 1.55
REALISTIC_MAX_BODY_LIFT_RATIO = 0.18
CONTAINER_VELOCITY_TAU = 0.05
CONTAINER_ACCELERATION_TAU = 0.075
CONTAINER_ACCELERATION_LIMIT = 80.0
CONTAINER_JERK_LIMIT = 2000.0
CONTAINER_STOP_TIME = 0.055


class LiquidSurfaceState:
    """Fourteen-point free surface that creates fluid-looking separation cheaply."""

    def __init__(self, node_count: int = LIQUID_NODE_COUNT) -> None:
        self.node_count = node_count
        self.heights = [0.0] * node_count
        self.velocities = [0.0] * node_count
        self.idle_phase = 0.0
        self.idle_speed = 1.0
        self.idle_weight = 1.0
        self.drag_tilt = 0.0
        self.vertical_compression = 0.0
        self.realistic_motion = False
        self.container_acceleration_x = 0.0
        self.container_acceleration_y = 0.0
        self.slosh_angle = 0.0
        self.slosh_angular_velocity = 0.0
        self.gravity_scale = 1.0
        self.bulk_offset_y = 0.0
        self.bulk_velocity_y = 0.0
        self.impact_strength = 0.0

    def reset(self) -> None:
        self.heights[:] = [0.0] * self.node_count
        self.velocities[:] = [0.0] * self.node_count
        self.idle_phase = 0.0
        self.idle_speed = 1.0
        self.idle_weight = 1.0
        self.drag_tilt = 0.0
        self.vertical_compression = 0.0
        self.container_acceleration_x = 0.0
        self.container_acceleration_y = 0.0
        self.slosh_angle = 0.0
        self.slosh_angular_velocity = 0.0
        self.gravity_scale = 1.0
        self.bulk_offset_y = 0.0
        self.bulk_velocity_y = 0.0
        self.impact_strength = 0.0

    def clear_motion(self) -> None:
        self.heights[:] = [0.0] * self.node_count
        self.velocities[:] = [0.0] * self.node_count
        self.drag_tilt = 0.0
        self.vertical_compression = 0.0
        self.container_acceleration_x = 0.0
        self.container_acceleration_y = 0.0
        self.slosh_angle = 0.0
        self.slosh_angular_velocity = 0.0
        self.gravity_scale = 1.0
        self.bulk_offset_y = 0.0
        self.bulk_velocity_y = 0.0
        self.impact_strength = 0.0

    def set_realistic_motion(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self.realistic_motion == enabled:
            return
        self.realistic_motion = enabled
        self.clear_motion()

    @staticmethod
    def _soft_clip(value: float, limit: float) -> float:
        return limit * math.tanh(value / limit)

    def apply_container_acceleration(self, acceleration_x: float, acceleration_y: float) -> None:
        if not self.realistic_motion:
            return
        self.container_acceleration_x = self._soft_clip(
            acceleration_x,
            CONTAINER_ACCELERATION_LIMIT,
        )
        self.container_acceleration_y = self._soft_clip(
            acceleration_y,
            CONTAINER_ACCELERATION_LIMIT,
        )
        self.idle_weight = min(self.idle_weight, 0.18)

    @property
    def activity(self) -> float:
        legacy_activity = max(
            max((abs(value) for value in self.heights), default=0.0),
            max((abs(value) for value in self.velocities), default=0.0) * 0.12,
            abs(self.drag_tilt),
            abs(self.vertical_compression),
        )
        if not self.realistic_motion:
            return legacy_activity
        return max(
            legacy_activity,
            abs(self.slosh_angle) * 0.5,
            abs(self.slosh_angular_velocity) * 0.05,
            abs(self.bulk_offset_y),
            abs(self.bulk_velocity_y) * 0.08,
            abs(self.gravity_scale - 1.0) * 0.06,
            abs(self.container_acceleration_x) * 0.001,
            abs(self.container_acceleration_y) * 0.001,
            self.impact_strength,
        )

    @property
    def settled(self) -> bool:
        legacy_settled = (
            max((abs(value) for value in self.heights), default=0.0) < SETTLE_HEIGHT_THRESHOLD
            and max((abs(value) for value in self.velocities), default=0.0)
            < SETTLE_VELOCITY_THRESHOLD
            and abs(self.drag_tilt) < SETTLE_HEIGHT_THRESHOLD
            and abs(self.vertical_compression) < SETTLE_HEIGHT_THRESHOLD
        )
        if not self.realistic_motion:
            return legacy_settled
        return (
            legacy_settled
            and abs(self.slosh_angle) < 0.0012
            and abs(self.slosh_angular_velocity) < 0.008
            and abs(self.bulk_offset_y) < 0.0012
            and abs(self.bulk_velocity_y) < 0.006
            and abs(self.gravity_scale - 1.0) < 0.002
            and abs(self.container_acceleration_x) < 0.05
            and abs(self.container_acceleration_y) < 0.05
            and self.impact_strength < 0.0012
        )

    def disturb(
        self,
        node_position: float,
        normalized_speed: float,
        direction_x: float,
    ) -> None:
        speed = max(0.0, min(7.0, normalized_speed))
        if speed <= 0:
            return
        width = min(
            MOUSE_SPLIT_MAX_WIDTH,
            MOUSE_SPLIT_MIN_WIDTH + speed * 0.28,
        )
        strength = min(0.46, speed * MOUSE_IMPULSE_STRENGTH)
        self.idle_weight = min(self.idle_weight, IDLE_WEIGHT_ACTIVE)
        direction = 1.0 if direction_x >= 0 else -1.0
        for index in range(self.node_count):
            offset = index - node_position
            distance = abs(offset)
            if distance > width * 1.2:
                continue
            # 中间向下、两肩向上；前肩略高、尾侧略深，形成分流而非单点橡皮筋凹陷。
            trough = math.exp(-((distance / max(0.01, width * 0.42)) ** 2))
            crest = math.exp(-(((distance - width * 0.72) / max(0.01, width * 0.22)) ** 2))
            directional_crest = 1.14 if offset * direction > 0 else 0.92
            trailing_wake = (
                math.exp(-(((offset + direction * width * 0.32) / (width * 0.34)) ** 2)) * 0.16
            )
            self.velocities[index] += strength * (
                trough + trailing_wake - crest * 0.82 * directional_crest
            )

    def add_drag_acceleration(self, acceleration_x: float, acceleration_y: float) -> None:
        tilt_impulse = acceleration_x * DRAG_INERTIA_STRENGTH
        compression_impulse = acceleration_y * DRAG_INERTIA_STRENGTH * 0.55
        self.idle_weight = min(self.idle_weight, 0.22)
        self.drag_tilt = max(
            -MAX_WAVE_HEIGHT * 2.25,
            min(
                MAX_WAVE_HEIGHT * 2.25,
                self.drag_tilt + acceleration_x * DRAG_INERTIA_STRENGTH,
            ),
        )
        self.vertical_compression = max(
            -MAX_WAVE_HEIGHT,
            min(
                MAX_WAVE_HEIGHT,
                self.vertical_compression + acceleration_y * DRAG_INERTIA_STRENGTH * 0.55,
            ),
        )
        for index in range(self.node_count):
            centered = index / (self.node_count - 1) - 0.5
            compression_shape = 1 - centered * centered * 4 - 2 / 3
            self.velocities[index] += (
                tilt_impulse * centered * 1.7 + compression_impulse * compression_shape * 0.8
            )

    def step(self, elapsed_seconds: float) -> None:
        dt = max(0.001, min(0.05, elapsed_seconds))
        if self.realistic_motion:
            self._step_realistic(dt)
            return
        self.idle_phase += dt * self.idle_speed
        previous = list(self.heights)
        velocity_damping = math.exp(-DAMPING * dt)
        target_idle_weight = 1.0 if self.activity < 0.012 else IDLE_WEIGHT_ACTIVE
        blend_rate = 1.6 if target_idle_weight > self.idle_weight else 8.0
        self.idle_weight += (target_idle_weight - self.idle_weight) * (
            1 - math.exp(-blend_rate * dt)
        )

        for index in range(self.node_count):
            progress = index / (self.node_count - 1)
            centered = progress - 0.5
            drag_target = self.drag_tilt * centered * 2
            # 上下加速度使用零均值的弧形目标，只产生压缩/回弹，不篡改平均额度。
            compression_shape = 1 - centered * centered * 4 - 2 / 3
            target = drag_target + self.vertical_compression * compression_shape
            left = previous[index - 1] if index > 0 else previous[index]
            right = previous[index + 1] if index < self.node_count - 1 else previous[index]
            neighbor_force = (left + right - previous[index] * 2) * WAVE_SPREAD
            acceleration = (target - previous[index]) * SPRING_STRENGTH + neighbor_force
            self.velocities[index] = (self.velocities[index] + acceleration * dt) * velocity_damping
            self.heights[index] = max(
                -MAX_WAVE_HEIGHT,
                min(
                    MAX_WAVE_HEIGHT,
                    previous[index] + self.velocities[index] * dt,
                ),
            )

        # 浮动只改变液面形状，平均高度必须继续精确表达真实额度。
        mean_height = sum(self.heights) / self.node_count
        mean_velocity = sum(self.velocities) / self.node_count
        self.heights[:] = [height - mean_height for height in self.heights]
        self.velocities[:] = [velocity - mean_velocity for velocity in self.velocities]
        self.drag_tilt *= math.exp(-5.4 * dt)
        self.vertical_compression *= math.exp(-7.0 * dt)

    def _step_realistic(self, dt: float) -> None:
        self.idle_phase += dt * self.idle_speed
        previous = list(self.heights)
        acceleration_x = self.container_acceleration_x
        acceleration_y = self.container_acceleration_y

        inertia_progress = max(
            0.0,
            min(
                1.0,
                (
                    math.hypot(acceleration_x, acceleration_y)
                    - REALISTIC_INERTIA_GAIN_START
                )
                / (REALISTIC_INERTIA_GAIN_END - REALISTIC_INERTIA_GAIN_START),
            ),
        )
        inertia_progress = inertia_progress * inertia_progress * (3 - 2 * inertia_progress)
        inertia_scale = REALISTIC_INERTIA_BASE_SCALE + inertia_progress * (
            REALISTIC_INERTIA_MAX_SCALE - REALISTIC_INERTIA_BASE_SCALE
        )
        inertial_acceleration_x = acceleration_x * inertia_scale
        inertial_acceleration_y = acceleration_y * inertia_scale
        effective_gravity_y = REALISTIC_GRAVITY - inertial_acceleration_y
        effective_gravity_magnitude = math.hypot(
            inertial_acceleration_x,
            effective_gravity_y,
        )
        target_gravity_scale = max(
            0.05,
            min(1.8, effective_gravity_magnitude / REALISTIC_GRAVITY),
        )
        self.gravity_scale += (target_gravity_scale - self.gravity_scale) * (
            1 - math.exp(-REALISTIC_GRAVITY_RESPONSE * dt)
        )

        target_angle = math.atan2(
            inertial_acceleration_x,
            effective_gravity_y,
        )
        angle_error = math.atan2(
            math.sin(target_angle - self.slosh_angle),
            math.cos(target_angle - self.slosh_angle),
        )
        response_frequency = REALISTIC_SLOSH_FREQUENCY * (
            1.0 + inertia_progress * 0.75
        )
        angular_acceleration = (
            response_frequency**2 * angle_error
            - 2
            * REALISTIC_SLOSH_DAMPING_RATIO
            * response_frequency
            * self.slosh_angular_velocity
        )
        self.slosh_angular_velocity += angular_acceleration * dt
        self.slosh_angle += self.slosh_angular_velocity * dt
        self.slosh_angle = math.atan2(
            math.sin(self.slosh_angle),
            math.cos(self.slosh_angle),
        )
        self.drag_tilt = math.sin(self.slosh_angle) * 0.5

        bulk_target = max(
            -REALISTIC_MAX_BULK_OFFSET,
            min(
                REALISTIC_MAX_BULK_OFFSET,
                -inertial_acceleration_y
                / REALISTIC_GRAVITY
                * REALISTIC_MAX_BULK_OFFSET,
            ),
        )
        bulk_acceleration = (
            (bulk_target - self.bulk_offset_y) * REALISTIC_BULK_STRENGTH
            - self.bulk_velocity_y * REALISTIC_BULK_DAMPING
        )
        previous_bulk_offset = self.bulk_offset_y
        self.bulk_velocity_y += bulk_acceleration * dt
        self.bulk_offset_y += self.bulk_velocity_y * dt

        impact = 0.0
        if (
            previous_bulk_offset < 0 <= self.bulk_offset_y
            and self.bulk_velocity_y > 0
            and self.gravity_scale > 1.0
        ):
            impact = min(
                0.12,
                self.bulk_velocity_y * 0.12 + (self.gravity_scale - 1.0) * 0.025,
            )
        if self.bulk_offset_y > REALISTIC_MAX_BULK_OFFSET:
            impact = max(impact, min(0.12, abs(self.bulk_velocity_y) * 0.09))
            self.bulk_offset_y = REALISTIC_MAX_BULK_OFFSET
            self.bulk_velocity_y *= -0.24
        elif self.bulk_offset_y < -REALISTIC_MAX_BULK_OFFSET:
            self.bulk_offset_y = -REALISTIC_MAX_BULK_OFFSET
            self.bulk_velocity_y *= -0.18
        self.impact_strength = max(
            impact,
            self.impact_strength * math.exp(-6.8 * dt),
        )

        vertical_target = max(
            -REALISTIC_MAX_WAVE_HEIGHT * 0.45,
            min(
                REALISTIC_MAX_WAVE_HEIGHT * 0.45,
                inertial_acceleration_y
                / REALISTIC_GRAVITY
                * REALISTIC_MAX_WAVE_HEIGHT
                * 0.18,
            ),
        )
        vertical_target -= self.impact_strength
        self.vertical_compression += (vertical_target - self.vertical_compression) * (
            1 - math.exp(-9.0 * dt)
        )

        velocity_damping = math.exp(
            -REALISTIC_NODE_DAMPING
            * (0.72 + min(1.5, self.gravity_scale) * 0.28)
            * dt
        )
        spring_strength = SPRING_STRENGTH * (0.28 + self.gravity_scale * 0.72)
        wave_spread = WAVE_SPREAD * (0.55 + min(1.5, self.gravity_scale) * 0.45)
        target_idle_weight = 1.0 if self.activity < 0.009 else 0.18
        blend_rate = 1.2 if target_idle_weight > self.idle_weight else 8.0
        self.idle_weight += (target_idle_weight - self.idle_weight) * (
            1 - math.exp(-blend_rate * dt)
        )

        for index in range(self.node_count):
            progress = index / (self.node_count - 1)
            centered = progress - 0.5
            # 整体方向由渲染路径旋转；角速度只制造一层滞后的自由液面波，
            # 避免转圈时把无限斜率重复叠加到已经旋转的水体上。
            tilt_target = self.slosh_angular_velocity * centered * 0.018
            compression_shape = 1 - centered * centered * 4 - 2 / 3
            target = (
                tilt_target
                + self.vertical_compression * compression_shape
                + self.bulk_offset_y * compression_shape * 1.1
            )
            left = previous[index - 1] if index > 0 else previous[index]
            right = previous[index + 1] if index < self.node_count - 1 else previous[index]
            neighbor_force = (left + right - previous[index] * 2) * wave_spread
            node_acceleration = (
                (target - previous[index]) * spring_strength + neighbor_force
            )
            self.velocities[index] = (
                self.velocities[index] + node_acceleration * dt
            ) * velocity_damping
            self.heights[index] = max(
                -REALISTIC_MAX_WAVE_HEIGHT,
                min(
                    REALISTIC_MAX_WAVE_HEIGHT,
                    previous[index] + self.velocities[index] * dt,
                ),
            )

        # 以零均值投影近似圆形容器内的面积守恒，避免晃动改变额度基准液位。
        mean_height = sum(self.heights) / self.node_count
        mean_velocity = sum(self.velocities) / self.node_count
        self.heights[:] = [height - mean_height for height in self.heights]
        self.velocities[:] = [velocity - mean_velocity for velocity in self.velocities]

        acceleration_decay = math.exp(-REALISTIC_ACCELERATION_DECAY * dt)
        self.container_acceleration_x *= acceleration_decay
        self.container_acceleration_y *= acceleration_decay


class FloatingUsageBall(QWidget):
    pressed = Signal(QPoint)
    dragged = Signal(QPoint)
    released = Signal(QPoint)
    resize_requested = Signal(int)

    def __init__(self, size: int = 88, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._today = "--"
        self._balance = "--"
        self._primary_label = "今日使用"
        self._secondary_label = "余额"
        self._quota_mode = False
        self._quota_remaining: float | None = None
        self._quota_reset_text = ""
        self._quota_title = "周额度"
        self._wave_phase = 0.0
        self._liquid_surface = LiquidSurfaceState()
        self._pointer_last_local: QPointF | None = None
        self._pointer_smoothed_velocity = QPointF()
        self._pointer_clock = QElapsedTimer()
        self._drag_last_global: QPointF | None = None
        self._drag_last_velocity = QPointF()
        self._drag_clock = QElapsedTimer()
        self._motion_provider_id = ""
        self._container_motion_active = False
        self._container_last_position: QPointF | None = None
        self._container_velocity = QPointF()
        self._container_acceleration = QPointF()
        self._container_clock = QElapsedTimer()
        self._wave_clock = QElapsedTimer()
        self._wave_clock.start()
        self._wave_timer = QTimer(self)
        self._wave_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._wave_timer.setInterval(ACTIVE_FRAME_INTERVAL_MS)
        self._wave_timer.timeout.connect(self._advance_wave)
        self._quota_geometry_cache: dict[float, tuple[QRectF, QPainterPath]] = {}
        self._water_gradient_cache: dict[
            tuple[str, str, float, float], QLinearGradient
        ] = {}
        self._quota_font_cache: dict[int, QFont] = {}
        self._debug_profile: dict[str, tuple[int, int]] = {}
        self._wheel_delta_remainder = 0
        self._water_shine_gradient = QLinearGradient(-36, 0, 36, 0)
        self._water_shine_gradient.setColorAt(0.0, QColor(255, 255, 255, 0))
        self._water_shine_gradient.setColorAt(0.38, QColor(255, 255, 255, 4))
        self._water_shine_gradient.setColorAt(0.5, QColor(225, 247, 255, 26))
        self._water_shine_gradient.setColorAt(0.62, QColor(255, 255, 255, 4))
        self._water_shine_gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
        self._deep_flow_gradient = QLinearGradient(-44, 0, 44, 0)
        self._refresh_deep_flow_gradient()
        self._internal_flow_center = QPointF(DESIGN_SIZE / 2, DESIGN_SIZE / 2)
        self._internal_flow_direction = QPointF(1, 0)
        self._internal_flow_velocity = QPointF()
        self._internal_flow_strength = 0.0
        self._internal_flow_age = 0.0
        self._glass_highlight_path = QPainterPath(QPointF(23, 47))
        self._glass_highlight_path.cubicTo(
            QPointF(27, 29),
            QPointF(43, 18),
            QPointF(68, 17),
        )
        self._peak_highlight = False
        self._hovered = False
        self._active = False
        theme_controller().changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _mode: str, _resolved: str) -> None:
        self._water_gradient_cache.clear()
        self._refresh_deep_flow_gradient()
        self.update()

    @staticmethod
    def _water_top_color(theme) -> QColor:
        default = LIGHT_THEME if theme.name == "light" else DARK_THEME
        if theme.accent.upper() == default.accent.upper():
            return QColor("#73BDFF" if theme.name == "light" else "#5CA6FF")
        return QColor(theme.accent).lighter(138 if theme.name == "light" else 126)

    def _refresh_deep_flow_gradient(self) -> None:
        theme = current_theme()
        default = LIGHT_THEME if theme.name == "light" else DARK_THEME
        deep = (
            QColor(8, 35, 98)
            if theme.accent.upper() == default.accent.upper()
            else QColor(theme.accent).darker(175)
        )
        self._deep_flow_gradient = QLinearGradient(-44, 0, 44, 0)
        for position, alpha in (
            (0.0, 0),
            (0.38, 3),
            (0.5, 24),
            (0.62, 3),
            (1.0, 0),
        ):
            color = QColor(deep)
            color.setAlpha(alpha)
            self._deep_flow_gradient.setColorAt(position, color)

    def _record_debug_profile(self, name: str, elapsed_ns: int) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        count, total_ns = self._debug_profile.get(name, (0, 0))
        count += 1
        total_ns += elapsed_ns
        if count >= 120:
            logger.debug(
                "Codex water ball %s average: %.3f ms",
                name,
                total_ns / count / 1_000_000,
            )
            count, total_ns = 0, 0
        self._debug_profile[name] = (count, total_ns)

    @property
    def realistic_motion_enabled(self) -> bool:
        return self._motion_provider_id == "codex"

    def _reset_container_motion(self) -> None:
        self._container_motion_active = False
        self._container_last_position = None
        self._container_velocity = QPointF()
        self._container_acceleration = QPointF()

    def set_motion_provider(self, provider_id: str) -> None:
        provider_id = str(provider_id or "").strip().lower()
        if self._motion_provider_id == provider_id:
            return
        self._motion_provider_id = provider_id
        self._reset_container_motion()
        self._liquid_surface.set_realistic_motion(provider_id == "codex")
        if self._quota_remaining is not None and self._quota_remaining > 0:
            ratio = self._quota_remaining / 100
            self._liquid_surface.idle_speed = (
                REALISTIC_IDLE_SPEED
                if self.realistic_motion_enabled
                else self._idle_flow_speed(ratio)
            )
            self._ensure_animation()
        self.update()

    @staticmethod
    def _motion_soft_clip(value: float) -> float:
        return CONTAINER_ACCELERATION_LIMIT * math.tanh(
            value / CONTAINER_ACCELERATION_LIMIT
        )

    def begin_container_motion(self, position: QPointF) -> bool:
        if (
            not self.realistic_motion_enabled
            or not self._quota_mode
            or self._quota_remaining is None
            or self._quota_remaining <= 0
        ):
            return False
        self._container_motion_active = True
        self._container_last_position = QPointF(position)
        self._container_velocity = QPointF()
        self._container_acceleration = QPointF()
        self._container_clock.restart()
        self._ensure_animation()
        return True

    def sample_container_motion(
        self,
        position: QPointF,
        elapsed_seconds: float | None = None,
    ) -> bool:
        if not self._container_motion_active or self._container_last_position is None:
            return False
        explicit_elapsed = elapsed_seconds is not None
        if elapsed_seconds is None:
            elapsed_ms = self._container_clock.elapsed()
            if 0 <= elapsed_ms < POINTER_SAMPLE_INTERVAL_MS:
                return False
            self._container_clock.restart()
            elapsed_seconds = 0.016 if elapsed_ms <= 0 else elapsed_ms / 1000
        dt = max(0.008, min(0.08, float(elapsed_seconds)))
        delta = QPointF(position) - self._container_last_position
        self._container_last_position = QPointF(position)
        side = max(1.0, min(self.width(), self.height()))
        raw_velocity = QPointF(
            max(-12.0, min(12.0, delta.x() / side / dt)),
            max(-12.0, min(12.0, delta.y() / side / dt)),
        )
        velocity_alpha = 1 - math.exp(-dt / CONTAINER_VELOCITY_TAU)
        previous_velocity = QPointF(self._container_velocity)
        velocity = QPointF(
            previous_velocity.x()
            + (raw_velocity.x() - previous_velocity.x()) * velocity_alpha,
            previous_velocity.y()
            + (raw_velocity.y() - previous_velocity.y()) * velocity_alpha,
        )
        raw_acceleration = QPointF(
            (velocity.x() - previous_velocity.x()) / dt,
            (velocity.y() - previous_velocity.y()) / dt,
        )
        acceleration_alpha = 1 - math.exp(-dt / CONTAINER_ACCELERATION_TAU)
        candidate_acceleration = QPointF(
            self._container_acceleration.x()
            + (raw_acceleration.x() - self._container_acceleration.x())
            * acceleration_alpha,
            self._container_acceleration.y()
            + (raw_acceleration.y() - self._container_acceleration.y())
            * acceleration_alpha,
        )
        max_acceleration_delta = CONTAINER_JERK_LIMIT * dt
        acceleration = QPointF(
            self._container_acceleration.x()
            + max(
                -max_acceleration_delta,
                min(
                    max_acceleration_delta,
                    candidate_acceleration.x() - self._container_acceleration.x(),
                ),
            ),
            self._container_acceleration.y()
            + max(
                -max_acceleration_delta,
                min(
                    max_acceleration_delta,
                    candidate_acceleration.y() - self._container_acceleration.y(),
                ),
            ),
        )
        acceleration = QPointF(
            self._motion_soft_clip(acceleration.x()),
            self._motion_soft_clip(acceleration.y()),
        )
        self._container_velocity = velocity
        self._container_acceleration = acceleration
        self._liquid_surface.apply_container_acceleration(
            acceleration.x(),
            acceleration.y(),
        )
        # Windows 拖动顶层窗口时可能暂停普通 Qt 定时器；仅记录加速度会让窗口
        # 被系统搬动，但液面一直沿用拖动前的缓存画面。这里消费距上一物理帧的
        # 实际时间并同步重绘，保证液面在拖动过程中就响应，而不是松手后才启动。
        wave_elapsed_ms = self._wave_clock.restart()
        if explicit_elapsed:
            wave_elapsed_seconds = dt
        elif wave_elapsed_ms <= 0:
            wave_elapsed_seconds = 0.001
        else:
            wave_elapsed_seconds = min(wave_elapsed_ms, 50) / 1000
        self._step_animation_state(wave_elapsed_seconds)
        self.repaint()
        self._ensure_animation()
        return True

    def end_container_motion(
        self,
        position: QPointF,
        elapsed_seconds: float | None = None,
    ) -> bool:
        if not self._container_motion_active:
            return False
        position = QPointF(position)
        if self._container_last_position is not None and position != self._container_last_position:
            self.sample_container_motion(position, elapsed_seconds)
        stop_acceleration = QPointF(
            self._motion_soft_clip(-self._container_velocity.x() / CONTAINER_STOP_TIME),
            self._motion_soft_clip(-self._container_velocity.y() / CONTAINER_STOP_TIME),
        )
        self._liquid_surface.apply_container_acceleration(
            stop_acceleration.x(),
            stop_acceleration.y(),
        )
        self._container_acceleration = stop_acceleration
        self._container_motion_active = False
        self._container_last_position = None
        self._container_velocity = QPointF()
        self._ensure_animation()
        return True

    def set_values(self, today: str, balance: str) -> None:
        if self._today == today and self._balance == balance:
            return
        self._today = today
        self._balance = balance
        self.update()

    def set_labels(self, primary: str, secondary: str) -> None:
        primary = str(primary)[:8]
        secondary = str(secondary)[:8]
        if (self._primary_label, self._secondary_label) == (primary, secondary):
            return
        self._primary_label = primary
        self._secondary_label = secondary
        self.update()

    @staticmethod
    def _compact_reset_text(value: str) -> str:
        return (
            str(value).replace(" 天 ", "天 ").replace(" 小时", "小时").replace(" 分钟", "分钟")[:10]
        )

    def set_quota_state(
        self,
        remaining_percent: float | None,
        reset_text: str,
        title: str = "周额度",
    ) -> None:
        remaining = (
            None if remaining_percent is None else max(0.0, min(100.0, float(remaining_percent)))
        )
        compact_reset = self._compact_reset_text(reset_text)
        compact_title = str(title).replace("每周额度", "周额度")[:8] or "周额度"
        state = (remaining, compact_reset, compact_title)
        if self._quota_mode and state == (
            self._quota_remaining,
            self._quota_reset_text,
            self._quota_title,
        ):
            return
        self._quota_mode = True
        self._quota_remaining, self._quota_reset_text, self._quota_title = state
        if remaining is None or remaining <= 0:
            # 空额度停止动画并清掉动量，避免下次恢复额度时复活旧余波。
            self._liquid_surface.reset()
            self._liquid_surface.idle_speed = 0.0
            self._reset_internal_flow()
        else:
            self._liquid_surface.idle_speed = (
                REALISTIC_IDLE_SPEED
                if self.realistic_motion_enabled
                else self._idle_flow_speed(remaining / 100)
            )
        remaining_text = "未知" if remaining is None else f"{remaining:.0f}%"
        self.setAccessibleName("Codex 剩余额度")
        self.setAccessibleDescription(remaining_text)
        self.setToolTip(remaining_text)
        if self.isVisible():
            if remaining is not None and remaining > 0:
                self._ensure_animation()
            else:
                self._wave_timer.stop()
        self.update()

    def clear_quota_state(self) -> None:
        if not self._quota_mode:
            return
        self._quota_mode = False
        self._quota_remaining = None
        self._quota_reset_text = ""
        self._wave_timer.stop()
        self._liquid_surface.reset()
        self._pointer_last_local = None
        self._pointer_smoothed_velocity = QPointF()
        self._drag_last_global = None
        self._drag_last_velocity = QPointF()
        self._reset_container_motion()
        self._reset_internal_flow()
        self.setAccessibleName("")
        self.setAccessibleDescription("")
        self.setToolTip("")
        self.update()

    def _ensure_animation(self) -> None:
        if self._quota_remaining is None or self._quota_remaining <= 0:
            return
        if self._active or not self._liquid_surface.settled or self._internal_flow_strength > 0.01:
            self._wave_timer.setInterval(ACTIVE_FRAME_INTERVAL_MS)
        if not self._wave_timer.isActive():
            self._wave_clock.restart()
            self._wave_timer.start()

    def _step_animation_state(self, elapsed_seconds: float) -> None:
        self._liquid_surface.step(elapsed_seconds)
        self._advance_internal_flow(elapsed_seconds)
        self._wave_phase = (self._liquid_surface.idle_phase * 0.4) % math.tau

    def _advance_wave(self) -> None:
        profile_timer = QElapsedTimer()
        if logger.isEnabledFor(logging.DEBUG):
            profile_timer.start()
        elapsed_ms = self._wave_clock.restart()
        elapsed_seconds = 0.016 if elapsed_ms <= 0 else min(elapsed_ms, 50) / 1000
        self._step_animation_state(elapsed_seconds)
        self.update()
        active_motion = (
            self._active
            or not self._liquid_surface.settled
            or self._internal_flow_strength > 0.01
        )
        interval = ACTIVE_FRAME_INTERVAL_MS if active_motion else IDLE_FRAME_INTERVAL_MS
        if self._wave_timer.interval() != interval:
            self._wave_timer.setInterval(interval)
        if not self._active and self._liquid_surface.settled:
            self._liquid_surface.clear_motion()
        if profile_timer.isValid():
            self._record_debug_profile("physics_update", profile_timer.nsecsElapsed())

    def _reset_internal_flow(self) -> None:
        self._internal_flow_center = QPointF(DESIGN_SIZE / 2, DESIGN_SIZE / 2)
        self._internal_flow_direction = QPointF(1, 0)
        self._internal_flow_velocity = QPointF()
        self._internal_flow_strength = 0.0
        self._internal_flow_age = 0.0

    def _advance_internal_flow(self, elapsed_seconds: float) -> None:
        dt = max(0.001, min(0.05, elapsed_seconds))
        if self._internal_flow_strength <= 0.001:
            self._internal_flow_strength = 0.0
            self._internal_flow_velocity = QPointF()
            return
        self._internal_flow_age += dt
        self._internal_flow_center += self._internal_flow_velocity * (dt * 0.28)
        velocity_decay = math.exp(-INTERNAL_FLOW_VELOCITY_DECAY * dt)
        self._internal_flow_velocity *= velocity_decay
        self._internal_flow_strength *= math.exp(-INTERNAL_FLOW_DECAY * dt)

    @staticmethod
    def _smoothstep(start: float, end: float, value: float) -> float:
        progress = max(0.0, min(1.0, (value - start) / (end - start)))
        return progress * progress * (3 - 2 * progress)

    @classmethod
    def _high_level_factor(cls, ratio: float) -> float:
        return cls._smoothstep(HIGH_LEVEL_FLOW_START, 1.0, ratio)

    @staticmethod
    def _idle_flow_scale(ratio: float) -> float:
        if ratio <= 0:
            return 0.0
        remaining_scale = 1.0 + (1.0 - ratio) * 1.5
        # 极低水位仍需限制波幅，避免静止波越过球底后看起来像凭空增加了额度。
        depth_scale = min(1.0, ratio / 0.06)
        return remaining_scale * depth_scale

    @staticmethod
    def _idle_flow_speed(ratio: float) -> float:
        if ratio <= 0:
            return 0.0
        return 1.0 + (1.0 - ratio) * 2.25

    @classmethod
    def _visual_surface_y(cls, inner: QRectF, ratio: float) -> float:
        actual_surface = inner.bottom() - inner.height() * ratio
        observation_weight = cls._smoothstep(HIGH_LEVEL_SURFACE_START, 1.0, ratio)
        # 高水位只增加一个很窄且单调缩小的观察带，使 travelling wave 可读；数字仍显示真实额度。
        return actual_surface + FULL_LEVEL_OBSERVATION_BAND_PX * observation_weight

    def _disturb_internal_flow(
        self,
        position: QPointF,
        velocity: QPointF,
        normalized_speed: float,
        ratio: float,
    ) -> None:
        magnitude = math.hypot(velocity.x(), velocity.y())
        if magnitude <= 0:
            return
        direction = QPointF(velocity.x() / magnitude, velocity.y() / magnitude)
        high_level = self._high_level_factor(ratio)
        momentum = min(34.0, 7.0 + normalized_speed * 5.2) * (0.82 + high_level * 0.18)
        # 保留部分旧动量，让突然反向时旧尾流与新流向短暂干涉，而不是瞬间翻转贴着鼠标走。
        carried_velocity = self._internal_flow_velocity * 0.42
        injected_velocity = direction * momentum
        combined_velocity = carried_velocity + injected_velocity
        combined_magnitude = math.hypot(combined_velocity.x(), combined_velocity.y())
        if combined_magnitude > 0.01:
            self._internal_flow_direction = combined_velocity / combined_magnitude
        self._internal_flow_velocity = combined_velocity
        self._internal_flow_center = QPointF(position)
        pointer_strength = min(1.0, 0.18 + normalized_speed * 0.14)
        self._internal_flow_strength = min(
            1.0,
            max(self._internal_flow_strength * 0.62, pointer_strength)
            * (0.86 + high_level * 0.14),
        )
        self._internal_flow_age = 0.0

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._quota_mode and self._quota_remaining is not None and self._quota_remaining > 0:
            self._ensure_animation()

    def hideEvent(self, event) -> None:
        self._wave_timer.stop()
        if self.realistic_motion_enabled:
            self._reset_container_motion()
            self._liquid_surface.clear_motion()
        super().hideEvent(event)

    def set_peak_highlight(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._peak_highlight == enabled:
            return
        self._peak_highlight = enabled
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self._pointer_last_local = QPointF(event.position())
        self._pointer_smoothed_velocity = QPointF()
        self._pointer_clock.restart()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._pointer_last_local = None
        self._pointer_smoothed_velocity = QPointF()
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.update()
        super().leaveEvent(event)

    def _liquid_inner_rect(self) -> QRectF:
        ball_radius = DESIGN_SIZE / 2 - (8 if self._peak_highlight else 3)
        inner, _ = self._quota_geometry(ball_radius)
        return QRectF(inner)

    def _disturb_surface_from_pointer(self, position: QPointF) -> bool:
        if self._pointer_last_local is None:
            self._pointer_last_local = QPointF(position)
            self._pointer_clock.restart()
            return False
        elapsed_ms = self._pointer_clock.elapsed()
        if 0 <= elapsed_ms < POINTER_SAMPLE_INTERVAL_MS:
            return False
        self._pointer_clock.restart()
        elapsed_seconds = 0.016 if elapsed_ms <= 0 else elapsed_ms / 1000
        delta = position - self._pointer_last_local
        self._pointer_last_local = QPointF(position)
        side = max(1.0, min(self.width(), self.height()))
        design_position = QPointF(
            position.x() * DESIGN_SIZE / side,
            position.y() * DESIGN_SIZE / side,
        )
        ball_radius = DESIGN_SIZE / 2 - (8 if self._peak_highlight else 3)
        inner, clip = self._quota_geometry(ball_radius)
        if not clip.contains(design_position) or self._quota_remaining is None:
            return False
        ratio = self._quota_remaining / 100
        if ratio <= 0:
            return False
        progress = max(
            0.0,
            min(1.0, (design_position.x() - inner.left()) / inner.width()),
        )
        node_position = progress * (self._liquid_surface.node_count - 1)
        low_index = int(node_position)
        high_index = min(self._liquid_surface.node_count - 1, low_index + 1)
        blend = node_position - low_index
        surface_offset = (
            self._liquid_surface.heights[low_index] * (1 - blend)
            + self._liquid_surface.heights[high_index] * blend
        ) * inner.height()
        surface_y = self._visual_surface_y(inner, ratio) + surface_offset
        if ratio < 0.995 and design_position.y() < surface_y - 5:
            return False
        raw_velocity = QPointF(
            delta.x() / side / elapsed_seconds,
            delta.y() / side / elapsed_seconds,
        )
        self._pointer_smoothed_velocity = QPointF(
            self._pointer_smoothed_velocity.x() * (1 - POINTER_VELOCITY_FILTER)
            + raw_velocity.x() * POINTER_VELOCITY_FILTER,
            self._pointer_smoothed_velocity.y() * (1 - POINTER_VELOCITY_FILTER)
            + raw_velocity.y() * POINTER_VELOCITY_FILTER,
        )
        normalized_speed = math.hypot(
            self._pointer_smoothed_velocity.x(),
            self._pointer_smoothed_velocity.y(),
        )
        if normalized_speed < 0.05:
            return False
        self._liquid_surface.disturb(
            node_position,
            normalized_speed,
            self._pointer_smoothed_velocity.x(),
        )
        self._disturb_internal_flow(
            design_position,
            self._pointer_smoothed_velocity,
            normalized_speed,
            ratio,
        )
        self._ensure_animation()
        return True

    def _sample_drag_motion(self, position: QPointF) -> None:
        if self._drag_last_global is None:
            self._drag_last_global = QPointF(position)
            self._drag_clock.restart()
            return
        elapsed_ms = self._drag_clock.restart()
        elapsed_seconds = 0.016 if elapsed_ms <= 0 else max(0.008, elapsed_ms / 1000)
        delta = position - self._drag_last_global
        self._drag_last_global = QPointF(position)
        side = max(1.0, min(self.width(), self.height()))
        raw_velocity = QPointF(
            max(-12.0, min(12.0, delta.x() / side / elapsed_seconds)),
            max(-12.0, min(12.0, delta.y() / side / elapsed_seconds)),
        )
        # 平滑采样速度以过滤 Windows 鼠标事件间隔抖动，同时保留启动和反向的加速度峰值。
        velocity = QPointF(
            self._drag_last_velocity.x() + (raw_velocity.x() - self._drag_last_velocity.x()) * 0.52,
            self._drag_last_velocity.y() + (raw_velocity.y() - self._drag_last_velocity.y()) * 0.52,
        )
        acceleration = QPointF(
            max(
                -80.0,
                min(80.0, (velocity.x() - self._drag_last_velocity.x()) / elapsed_seconds),
            ),
            max(
                -80.0,
                min(80.0, (velocity.y() - self._drag_last_velocity.y()) / elapsed_seconds),
            ),
        )
        self._drag_last_velocity = velocity
        # 使用容器加速度而不是鼠标位置：匀速阶段外力自然归零，启动/停止/反向最明显。
        self._liquid_surface.add_drag_acceleration(
            acceleration.x(),
            acceleration.y(),
        )
        self._ensure_animation()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._active = True
            self._drag_last_global = QPointF(event.globalPosition())
            self._drag_last_velocity = QPointF()
            self._drag_clock.restart()
            self._ensure_animation()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()
            self.pressed.emit(event.globalPosition().toPoint())
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        profile_timer = QElapsedTimer()
        if logger.isEnabledFor(logging.DEBUG):
            profile_timer.start()
        try:
            if event.buttons() & Qt.MouseButton.LeftButton:
                if not self.realistic_motion_enabled:
                    self._sample_drag_motion(event.globalPosition())
                self.dragged.emit(event.globalPosition().toPoint())
                event.accept()
                return
            self._disturb_surface_from_pointer(event.position())
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            super().mouseMoveEvent(event)
        finally:
            if profile_timer.isValid():
                self._record_debug_profile("mouseMoveEvent", profile_timer.nsecsElapsed())

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if not self.realistic_motion_enabled:
                # 旧供应商继续使用原有鼠标停止冲量，避免改变既有手感。
                stop_acceleration_x = -self._drag_last_velocity.x() / 0.045
                stop_acceleration_y = -self._drag_last_velocity.y() / 0.045
                self._liquid_surface.add_drag_acceleration(
                    stop_acceleration_x,
                    stop_acceleration_y,
                )
                self._ensure_animation()
            self._active = False
            self._drag_last_global = None
            self._drag_last_velocity = QPointF()
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.update()
            self.released.emit(event.globalPosition().toPoint())
            event.accept()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        self._wheel_delta_remainder += delta
        steps = math.trunc(self._wheel_delta_remainder / 120)
        if steps:
            self._wheel_delta_remainder -= steps * 120
            self.resize_requested.emit(steps)
        event.accept()

    @staticmethod
    def _smooth_surface_path(rect: QRectF, surface_y: float, offsets: list[float]) -> QPainterPath:
        points = [
            QPointF(
                rect.left() + rect.width() * index / (len(offsets) - 1),
                surface_y + offset,
            )
            for index, offset in enumerate(offsets)
        ]
        path = QPainterPath(points[0])
        # Catmull-Rom 转三次贝塞尔，保证节点能传播尖锐冲量但绘制出来仍是连续液面。
        for index in range(len(points) - 1):
            previous = points[index - 1] if index > 0 else points[index]
            current = points[index]
            following = points[index + 1]
            after = points[index + 2] if index + 2 < len(points) else following
            control_one = current + (following - previous) / 6
            control_two = following - (after - current) / 6
            path.cubicTo(control_one, control_two, following)
        return path

    def _idle_surface_offsets(
        self,
        rect: QRectF,
        phase_shift: float = 0.0,
    ) -> list[float]:
        time = self._liquid_surface.idle_phase
        weight = self._liquid_surface.idle_weight
        offsets = [
            weight
            * (
                # 主波约 5.8 秒穿过一个球宽，且可见范围内只有一个宽缓波峰/波谷。
                math.sin(x * 0.047 - time * 0.84 + phase_shift)
                * IDLE_WAVE_PRIMARY_AMPLITUDE
                + math.sin(x * 0.030 + time * 0.34 + 1.6 + phase_shift * 0.63)
                * IDLE_WAVE_SECONDARY_AMPLITUDE
                + math.sin(x * 0.071 - time * 0.22 + 3.1 - phase_shift * 0.4)
                * IDLE_WAVE_TERTIARY_AMPLITUDE
            )
            for x in (
                rect.left() + rect.width() * index / (self._liquid_surface.node_count - 1)
                for index in range(self._liquid_surface.node_count)
            )
        ]
        # 三组双向行波在有限节点上并非天然零均值；去掉均值才能保证 idle 不篡改额度水位。
        mean_offset = sum(offsets) / len(offsets)
        return [offset - mean_offset for offset in offsets]

    def _surface_paths(
        self,
        rect: QRectF,
        surface_y: float,
        ratio: float,
        back_layer: bool = False,
    ) -> tuple[QPainterPath, QPainterPath]:
        surface_scale = min(
            1.0,
            ratio
            / (
                REALISTIC_FULL_RESPONSE_RATIO
                if self.realistic_motion_enabled
                else 0.16
            ),
        )
        physics_scale = surface_scale * (1 - self._high_level_factor(ratio) * 0.52)
        idle_scale = (
            REALISTIC_IDLE_SCALE * min(1.0, ratio / 0.06)
            if self.realistic_motion_enabled
            else self._idle_flow_scale(ratio)
        )
        layer_scale = 0.62 if back_layer else 1.0
        layer_shift = -1.2 if back_layer else 0.6
        idle_offsets = self._idle_surface_offsets(rect, 0.72 if back_layer else 0.0)
        vertical_offsets = [0.0] * self._liquid_surface.node_count
        if self.realistic_motion_enabled:
            vertical_signal = (
                self._liquid_surface.bulk_offset_y * 0.35
                - self._liquid_surface.impact_strength * 0.72
            )
            vertical_offsets = [
                vertical_signal
                * (1 - (index / (self._liquid_surface.node_count - 1) - 0.5) ** 2 * 4 - 2 / 3)
                * rect.height()
                * 1.15
                for index in range(self._liquid_surface.node_count)
            ]
            mean_vertical_offset = sum(vertical_offsets) / len(vertical_offsets)
            vertical_offsets = [
                offset - mean_vertical_offset for offset in vertical_offsets
            ]
        offsets = [
            height * rect.height() * layer_scale * physics_scale
                + vertical_offsets[index] * layer_scale * physics_scale
                + idle_offsets[index] * (0.76 if back_layer else 1.0) * idle_scale
            for index, height in enumerate(self._liquid_surface.heights)
        ]
        body_lift = self._realistic_body_lift(rect)
        surface = self._smooth_surface_path(
            rect,
            surface_y + layer_shift - body_lift,
            offsets,
        )
        fill = QPainterPath(surface)
        fill_bottom = rect.bottom() + 1 - body_lift
        fill.lineTo(rect.right(), fill_bottom)
        fill.lineTo(rect.left(), fill_bottom)
        fill.closeSubpath()
        if self.realistic_motion_enabled:
            transform = self._realistic_body_transform(rect)
            surface = transform.map(surface)
            fill = transform.map(fill)
        return fill, surface

    def _realistic_body_transform(self, rect: QRectF) -> QTransform:
        transform = QTransform()
        if not self.realistic_motion_enabled:
            return transform
        center = rect.center()
        transform.translate(center.x(), center.y())
        transform.rotate(math.degrees(self._liquid_surface.slosh_angle))
        transform.translate(-center.x(), -center.y())
        return transform

    def _realistic_body_lift(self, rect: QRectF) -> float:
        if not self.realistic_motion_enabled:
            return 0.0
        upright_weight = max(0.0, math.cos(self._liquid_surface.slosh_angle))
        return min(
            rect.height() * REALISTIC_MAX_BODY_LIFT_RATIO,
            max(0.0, -self._liquid_surface.bulk_offset_y)
            * rect.height()
            * REALISTIC_BODY_LIFT_SCALE,
        ) * upright_weight

    def _subsurface_highlight_path(
        self,
        rect: QRectF,
        surface_y: float,
        ratio: float,
    ) -> QPainterPath:
        surface_scale = min(
            1.0,
            ratio
            / (
                REALISTIC_FULL_RESPONSE_RATIO
                if self.realistic_motion_enabled
                else 0.16
            ),
        )
        physics_scale = surface_scale * (1 - self._high_level_factor(ratio) * 0.52)
        idle_scale = self._idle_flow_scale(ratio)
        idle_offsets = self._idle_surface_offsets(rect, 1.1)
        offsets = [
            height * rect.height() * 0.68 * physics_scale
            + idle_offsets[index] * 0.72 * idle_scale
            for index, height in enumerate(self._liquid_surface.heights)
        ]
        path = self._smooth_surface_path(
            rect,
            surface_y + 3.2 - self._realistic_body_lift(rect),
            offsets,
        )
        if self.realistic_motion_enabled:
            path = self._realistic_body_transform(rect).map(path)
        return path

    def _internal_split_paths(self, inner: QRectF) -> tuple[QPainterPath, QPainterPath]:
        direction = self._internal_flow_direction
        normal = QPointF(-direction.y(), direction.x())
        center = self._internal_flow_center
        strength = self._internal_flow_strength
        length = inner.width() * (0.13 + strength * 0.12)
        # 两股流先分开、随后带阻尼地交叉回拢；负值阶段形成一次克制的二次碰撞。
        separation = (
            inner.width()
            * (0.025 + strength * 0.055)
            * math.cos(self._internal_flow_age * 7.2)
            * math.exp(-self._internal_flow_age * 0.48)
        )
        tail = center - direction * length
        head = center + direction * (length * 0.78)
        paths: list[QPainterPath] = []
        for side in (-1.0, 1.0):
            path = QPainterPath(tail)
            path.cubicTo(
                center - direction * (length * 0.28) + normal * (separation * side),
                center + direction * (length * 0.28) + normal * (separation * side),
                head,
            )
            paths.append(path)
        return paths[0], paths[1]

    def _paint_internal_flow(
        self,
        painter: QPainter,
        theme,
        inner: QRectF,
        clip: QPainterPath,
        ratio: float,
    ) -> None:
        high_level = self._high_level_factor(ratio)
        pointer_strength = self._internal_flow_strength
        if pointer_strength <= 0.01:
            return
        painter.save()
        painter.setClipPath(clip)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        split_paths = self._internal_split_paths(inner)
        for index, split_path in enumerate(split_paths):
            split_color = QColor("#FFFFFF" if index == 0 else theme.accent_hover)
            split_color.setAlpha(round((18 + high_level * 54) * pointer_strength))
            painter.setPen(
                QPen(
                    split_color,
                    2.6 + high_level * 2.0 + pointer_strength * 1.4,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            painter.drawPath(split_path)

        wake_color = QColor(theme.accent)
        wake_color.setAlpha(round((12 + high_level * 30) * pointer_strength))
        direction = self._internal_flow_direction
        wake_start = self._internal_flow_center - direction * (
            inner.width() * (0.17 + pointer_strength * 0.12)
        )
        wake_end = self._internal_flow_center - direction * (inner.width() * 0.035)
        painter.setPen(
            QPen(
                wake_color,
                4.5 + high_level * 2.5,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        painter.drawLine(wake_start, wake_end)
        painter.restore()

    @staticmethod
    def _paint_centered_text(
        painter: QPainter,
        rect: QRectF,
        text: str,
        color: QColor,
        shadow: QColor,
    ) -> None:
        painter.setPen(shadow)
        painter.drawText(
            rect.translated(0, 1),
            Qt.AlignmentFlag.AlignCenter,
            text,
        )
        painter.setPen(color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _quota_geometry(self, ball_radius: float) -> tuple[QRectF, QPainterPath]:
        key = round(ball_radius, 2)
        cached = self._quota_geometry_cache.get(key)
        if cached is not None:
            return cached
        inner_margin = DESIGN_SIZE / 2 - ball_radius + 3
        inner = QRectF(
            inner_margin,
            inner_margin,
            DESIGN_SIZE - inner_margin * 2,
            DESIGN_SIZE - inner_margin * 2,
        )
        clip = QPainterPath()
        clip.addEllipse(inner)
        self._quota_geometry_cache[key] = (inner, clip)
        return inner, clip

    def _water_gradient(self, theme, surface_y: float, bottom: float) -> QLinearGradient:
        key = (theme.name, theme.accent, round(surface_y, 2), round(bottom, 2))
        cached = self._water_gradient_cache.get(key)
        if cached is not None:
            return cached
        water_top = self._water_top_color(theme)
        water_top.setAlpha(205 if theme.name == "light" else 218)
        upper = QColor(theme.accent_hover)
        upper.setAlpha(224)
        middle = QColor(theme.accent)
        middle.setAlpha(236)
        deep = QColor(theme.accent).darker(138)
        deep.setAlpha(248)
        water = QLinearGradient(0, surface_y, 0, bottom)
        water.setColorAt(0.0, water_top)
        water.setColorAt(0.2, upper)
        water.setColorAt(0.64, middle)
        water.setColorAt(1.0, deep)
        self._water_gradient_cache[key] = water
        return water

    def _paint_water_shine(
        self,
        painter: QPainter,
        inner: QRectF,
        water_path: QPainterPath,
        high_level: float,
    ) -> None:
        shine_progress = (self._liquid_surface.idle_phase % 12.0) / 12.0
        shine_margin = inner.width() * 0.45
        shine_x = inner.left() - shine_margin + shine_progress * (inner.width() + shine_margin * 2)
        painter.save()
        painter.setClipPath(water_path)
        painter.setOpacity(0.42 + high_level * 0.18)
        painter.translate(
            shine_x + self._internal_flow_velocity.x() * 0.045,
            inner.center().y() + self._internal_flow_velocity.y() * 0.025,
        )
        painter.shear(-0.18, 0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._water_shine_gradient)
        painter.drawRect(QRectF(-36, -inner.height(), 72, inner.height() * 2))
        painter.restore()

    def _paint_deep_flow(
        self,
        painter: QPainter,
        inner: QRectF,
        water_path: QPainterPath,
        high_level: float,
    ) -> None:
        deep_progress = (self._liquid_surface.idle_phase % 16.0) / 16.0
        deep_margin = inner.width() * 0.5
        deep_x = inner.right() + deep_margin - deep_progress * (
            inner.width() + deep_margin * 2
        )
        painter.save()
        painter.setClipPath(water_path)
        painter.setOpacity(0.38 + high_level * 0.18)
        painter.translate(
            deep_x - self._internal_flow_velocity.x() * 0.035,
            inner.center().y() - self._internal_flow_velocity.y() * 0.02,
        )
        painter.shear(0.12, 0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._deep_flow_gradient)
        painter.drawRect(QRectF(-44, 0, 88, inner.height()))
        painter.restore()

    def _paint_glass_highlight(self, painter: QPainter) -> None:
        highlight = QColor(255, 255, 255, 38 if self._hovered else 28)
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                highlight,
                1.8,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        painter.drawPath(self._glass_highlight_path)
        painter.restore()

    def _paint_quota(self, painter: QPainter, theme, ball_radius: float) -> None:
        inner, clip = self._quota_geometry(ball_radius)
        water_path = QPainterPath()
        if self._quota_remaining is not None and self._quota_remaining > 0:
            ratio = self._quota_remaining / 100
            surface_y = self._visual_surface_y(inner, ratio)
            high_level = self._high_level_factor(ratio)

            painter.save()
            painter.setClipPath(clip)
            water_top = QColor(theme.heat[3] if theme.name == "light" else theme.accent_hover)
            body_lift = self._realistic_body_lift(inner)
            water = self._water_gradient(
                theme,
                surface_y - body_lift,
                inner.bottom() - body_lift,
            )

            back_path, _back_surface = self._surface_paths(
                inner, surface_y, ratio, back_layer=True
            )
            water_path, water_surface = self._surface_paths(inner, surface_y, ratio)

            back_color = QColor(water_top)
            back_color.setAlpha(184)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(back_color)
            painter.drawPath(back_path)
            painter.setBrush(water)
            painter.drawPath(water_path)
            self._paint_deep_flow(painter, inner, water_path, high_level)
            self._paint_water_shine(painter, inner, water_path, high_level)
            self._paint_internal_flow(painter, theme, inner, water_path, ratio)

            subsurface = self._subsurface_highlight_path(inner, surface_y, ratio)
            default = LIGHT_THEME if theme.name == "light" else DARK_THEME
            if theme.accent.upper() == default.accent.upper():
                subsurface_color = QColor(220, 245, 255, 34)
            else:
                subsurface_color = QColor(theme.accent).lighter(175)
                subsurface_color.setAlpha(34)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(
                QPen(
                    subsurface_color,
                    1.0,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            painter.drawPath(subsurface)

            surface_highlight = QColor("#FFFFFF")
            surface_highlight.setAlpha(
                round(92 + min(1.0, self._liquid_surface.activity * 5) * 38)
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(
                QPen(
                    surface_highlight,
                    1.25,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            painter.drawPath(water_surface)
            painter.restore()

        inner_rim = QColor(theme.accent_hover if theme.name == "light" else "#FFFFFF")
        inner_rim.setAlpha(105 if theme.name == "light" else 78)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(inner_rim, 1.2))
        painter.drawEllipse(inner.adjusted(0.7, 0.7, -0.7, -0.7))

        percentage = "--" if self._quota_remaining is None else f"{self._quota_remaining:.0f}%"
        value_size = 25 if len(percentage) <= 3 else 22 if len(percentage) <= 4 else 20
        value_font = self._quota_font_cache.get(value_size)
        if value_font is None:
            value_font = QFont("Microsoft YaHei UI", value_size, QFont.Weight.Bold)
            self._quota_font_cache[value_size] = value_font
        painter.setFont(value_font)
        value_rect = QRectF(8, 36, 104, 48)
        empty_shadow = QColor("#000000" if theme.name == "dark" else "#FFFFFF")
        empty_shadow.setAlpha(130)
        water_shadow = QColor("#000000")
        water_shadow.setAlpha(145)

        if water_path.isEmpty():
            self._paint_centered_text(
                painter,
                value_rect,
                percentage,
                QColor(theme.value),
                empty_shadow,
            )
            return

        # 同一数字按空气和液体区域各绘制一次，液面穿过文字时仍保持逐像素对比度。
        empty_path = clip.subtracted(water_path)
        painter.save()
        painter.setClipPath(empty_path)
        self._paint_centered_text(
            painter,
            value_rect,
            percentage,
            QColor(theme.value),
            empty_shadow,
        )
        painter.restore()

        painter.save()
        painter.setClipPath(water_path.intersected(clip))
        self._paint_centered_text(
            painter,
            value_rect,
            percentage,
            QColor("#FFFFFF"),
            water_shadow,
        )
        painter.restore()

    def paintEvent(self, _event) -> None:
        profile_timer = QElapsedTimer()
        if logger.isEnabledFor(logging.DEBUG):
            profile_timer.start()
        theme = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        side = min(self.width(), self.height())
        painter.scale(side / DESIGN_SIZE, side / DESIGN_SIZE)
        side = DESIGN_SIZE
        center = QPointF(side / 2, side / 2)

        # The light warning token is tuned for text contrast and looks brown as
        # emitted light; use saturated amber for the peak glow instead.
        peak_color = QColor("#FFB000" if theme.name == "light" else theme.warning)
        ball_radius = side / 2 - (8 if self._peak_highlight else 3)
        if self._peak_highlight:
            # Keep the outermost pixels transparent so antialiasing is completed
            # inside the widget instead of being clipped into a jagged edge.
            halo_radius = side / 2 - 2
            halo = QRadialGradient(center, halo_radius)
            transparent_warning = QColor(peak_color)
            transparent_warning.setAlpha(0)
            soft_warning = QColor(peak_color)
            soft_warning.setAlpha(64 if self._hovered else 48)
            bright_warning = QColor(peak_color)
            bright_warning.setAlpha(220 if self._hovered else 190)
            outer_warning = QColor(peak_color)
            outer_warning.setAlpha(82 if self._hovered else 62)
            halo.setColorAt(0.82, transparent_warning)
            halo.setColorAt(0.88, soft_warning)
            halo.setColorAt(0.93, bright_warning)
            halo.setColorAt(0.97, outer_warning)
            halo.setColorAt(1.0, transparent_warning)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(center, halo_radius, halo_radius)
        else:
            glow = QColor(theme.accent)
            glow.setAlpha(24 if self._active else 70 if self._hovered else 36)
            painter.setPen(QPen(glow, 4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, side / 2 - 3, side / 2 - 3)

        outer = QRadialGradient(center, ball_radius)
        outer.setColorAt(0.0, QColor(theme.elevated))
        outer.setColorAt(0.72, QColor(theme.surface))
        outer.setColorAt(1.0, QColor(theme.window))
        painter.setBrush(outer)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, ball_radius, ball_radius)

        if self._peak_highlight:
            border_color = QColor(peak_color)
            border_color.setAlpha(210 if self._active else 235)
            painter.setPen(QPen(border_color, 3))
        else:
            border = (
                theme.accent_hover
                if self._quota_mode and self._hovered
                else theme.border_hover
                if self._hovered
                else theme.accent
            )
            painter.setPen(QPen(QColor(border), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, ball_radius, ball_radius)

        if self._quota_mode:
            self._paint_quota(painter, theme, ball_radius)
            self._paint_glass_highlight(painter)
        else:
            highlight = QLinearGradient(0, 8, 0, side * 0.55)
            highlight_start = QColor(theme.accent)
            highlight_start.setAlpha(42)
            highlight_end = QColor(theme.accent)
            highlight_end.setAlpha(0)
            highlight.setColorAt(0.0, highlight_start)
            highlight.setColorAt(1.0, highlight_end)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(highlight)
            painter.drawEllipse(QRectF(16, 12, side - 32, side * 0.42))

            painter.setPen(QColor(theme.subtext))
            painter.setFont(QFont("Microsoft YaHei UI", 9))
            painter.drawText(
                QRectF(10, 18, side - 20, 18),
                Qt.AlignmentFlag.AlignCenter,
                self._primary_label,
            )

            painter.setPen(QColor(theme.value))
            value_size = 16 if len(self._today) <= 8 else 12
            painter.setFont(QFont("Microsoft YaHei UI", value_size, QFont.Weight.Bold))
            painter.drawText(
                QRectF(8, 34, side - 16, 25),
                Qt.AlignmentFlag.AlignCenter,
                self._today,
            )

            painter.setPen(QPen(QColor(theme.border), 1))
            painter.drawLine(QPointF(side * 0.25, 64), QPointF(side * 0.75, 64))
            painter.setPen(QColor(theme.subtext))
            painter.setFont(QFont("Microsoft YaHei UI", 8))
            painter.drawText(
                QRectF(10, 65, side - 20, 15),
                Qt.AlignmentFlag.AlignCenter,
                self._secondary_label,
            )
            painter.setPen(QColor(theme.accent_hover))
            balance_size = 11 if len(self._balance) <= 8 else 9
            painter.setFont(
                QFont("Microsoft YaHei UI", balance_size, QFont.Weight.DemiBold)
            )
            painter.drawText(
                QRectF(14, 80, side - 28, 19),
                Qt.AlignmentFlag.AlignCenter,
                self._balance,
            )
        painter.end()
        if profile_timer.isValid():
            self._record_debug_profile("paintEvent", profile_timer.nsecsElapsed())
