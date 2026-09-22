"""Semantic color tokens（Fluent 2 风格，Light / Dark 两套完整取值）。

约束：
- 业务代码只使用 semantic name（如 ``surface`` / ``danger``），不直接写 hex。
- Dark 不是 Light 的反色，而是独立设计的一套 neutral / accent 层级。
- status 色在两种主题下语义一致（success 永远表示成功，不随主题变色义）。
- 对比度：secondary / disabled 使用比旧 ``#95a5a6`` / ``#b0b7bf`` 更高的对比。

Light accent 保留项目既有品牌蓝 ``#2c6fbb``（兼容旧 QSS / 现有测试），
其余 neutral 层级按 Fluent 2 重新建立。
"""

from __future__ import annotations

LIGHT = "light"
DARK = "dark"

LIGHT_COLORS: dict[str, str] = {
    # ---- neutral / surfaces ----
    "background": "#f5f7fa",
    "surface": "#ffffff",
    "surface_alt": "#eef2f6",
    "surface_hover": "#e8edf3",
    "surface_pressed": "#dce4ec",
    "surface_selected": "#e3edf8",
    # ---- text ----
    "text_primary": "#1f2937",
    "text_secondary": "#56606d",
    "text_tertiary": "#6f7a87",
    "text_disabled": "#9aa3ae",
    "text_on_accent": "#ffffff",
    # ---- borders ----
    "border": "#d5dbe2",
    "border_subtle": "#e4e9ef",
    "border_strong": "#b9c2cd",
    # ---- accent ----
    "accent": "#2c6fbb",
    "accent_hover": "#255e9e",
    "accent_pressed": "#1f508a",
    "accent_disabled": "#a9c4e4",
    # ---- status ----
    "success": "#107c41",
    "success_background": "#e7f4ec",
    "warning": "#9a6700",
    "warning_background": "#fff6e6",
    "danger": "#c42b1c",
    "danger_background": "#fdeceb",
    "info": "#0f6cbd",
    "info_background": "#e8f1fb",
    # ---- misc ----
    "focus": "#2c6fbb",
    "overlay": "rgba(0, 0, 0, 0.35)",
}

DARK_COLORS: dict[str, str] = {
    # ---- neutral / surfaces ----
    "background": "#1c1c1e",
    "surface": "#26262a",
    "surface_alt": "#2d2d31",
    "surface_hover": "#34343a",
    "surface_pressed": "#3c3c42",
    "surface_selected": "#2b3a4d",
    # ---- text ----
    "text_primary": "#f3f3f5",
    "text_secondary": "#c5c8ce",
    "text_tertiary": "#9aa0a8",
    "text_disabled": "#6b7079",
    "text_on_accent": "#ffffff",
    # ---- borders ----
    "border": "#3a3a40",
    "border_subtle": "#323237",
    "border_strong": "#4a4a52",
    # ---- accent ----
    "accent": "#6cb8ff",
    "accent_hover": "#8ac8ff",
    "accent_pressed": "#4fa8f5",
    "accent_disabled": "#3f5a72",
    # ---- status ----
    "success": "#54b054",
    "success_background": "#1e2f22",
    "warning": "#e0a53c",
    "warning_background": "#33291a",
    "danger": "#f1707a",
    "danger_background": "#3a2023",
    "info": "#6cb8ff",
    "info_background": "#1d2c3a",
    # ---- misc ----
    "focus": "#6cb8ff",
    "overlay": "rgba(0, 0, 0, 0.55)",
}

# Canonical key set. Light / Dark 必须逐字一致（由测试保证）。
COLOR_KEYS: tuple[str, ...] = tuple(LIGHT_COLORS.keys())


def colors_for(theme: str) -> dict[str, str]:
    """返回指定主题的颜色 token。未知主题抛 ValueError。"""
    if theme == LIGHT:
        return dict(LIGHT_COLORS)
    if theme == DARK:
        return dict(DARK_COLORS)
    raise ValueError(f"unknown theme: {theme!r}")
