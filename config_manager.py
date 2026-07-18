"""Backward-compatible alias for the split configuration package."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.runtime import *  # noqa: F403 - 静态检查需要识别兼容入口的公开属性
else:
    from config import runtime as _runtime

    # 保留模块对象别名，使旧调用方对私有测试钩子和运行时路径的修改仍作用于同一状态。
    sys.modules[__name__] = _runtime
