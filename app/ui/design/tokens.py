"""Token 汇总：把 colors / typography / radius 组合成 QSS render map。

``render_map(theme)`` 返回 ``{placeholder: value}``，供 ThemeManager 做安全替换。
placeholder 名称即 QSS 中的 ``{{name}}``。
"""

from __future__ import annotations

from . import colors as _colors
from . import radius as _radius
from . import typography as _typography


def render_map(theme: str) -> dict[str, str]:
    """返回指定主题的完整 placeholder → value 映射。"""
    mapping: dict[str, str] = {}
    mapping.update(_colors.colors_for(theme))

    # typography（QSS 侧只需要族与基础字号；role font 由组件用 QFont 应用）
    mapping["font_family"] = _typography.FONT_FAMILY
    mapping["font_mono"] = _typography.FONT_FAMILY_MONO
    mapping["font_size_body"] = f"{_typography.size_for(_typography.BODY)}px"
    mapping["font_size_caption"] = f"{_typography.size_for(_typography.CAPTION)}px"

    # radius
    mapping["radius_small"] = f"{_radius.SMALL}px"
    mapping["radius_medium"] = f"{_radius.MEDIUM}px"
    mapping["radius_large"] = f"{_radius.LARGE}px"

    return mapping


def color_keys() -> tuple[str, ...]:
    return _colors.COLOR_KEYS
