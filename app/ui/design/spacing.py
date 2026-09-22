"""Spacing scale（固定刻度，禁止随意的 magic number）。

使用：``layout.setSpacing(spacing.SM)``、``setContentsMargins(spacing.LG, ...)``。
"""

from __future__ import annotations

XS = 4
SM = 8
MD = 12
LG = 16
XL = 20
XXL = 24
XXXL = 32
SECTION = 40
PAGE = 48

SCALE: tuple[int, ...] = (XS, SM, MD, LG, XL, XXL, XXXL, SECTION, PAGE)
