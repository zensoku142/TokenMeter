"""Backward-compatible alias for the split configuration package."""

from __future__ import annotations

import sys

from config import runtime as _runtime

# 保留模块对象别名，使旧调用方对私有测试钩子和运行时路径的修改仍作用于同一状态。
sys.modules[__name__] = _runtime
