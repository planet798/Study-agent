"""Semantic warm-neutral color tokens for Light and Dark themes.

- UI code uses semantic names instead of literal colors.
- Dark is an independently tuned palette, not an inverted Light theme.
- Primary actions use dedicated neutral tokens; accent remains for links, focus,
  and meaningful accent states.
- Status colors retain their semantic meaning across both themes.
- The existing Light brand accent is retained for compatibility.
"""

from __future__ import annotations

LIGHT = "light"
DARK = "dark"

LIGHT_COLORS: dict[str, str] = {
    # ---- warm neutral / surfaces ----
    "background": "#faf9f7",
    "surface": "#ffffff",
    "surface_alt": "#f3f1ed",
    "surface_hover": "#ebe9e4",
    "surface_pressed": "#e1ded8",
    "surface_selected": "#eae7e1",
    # ---- text ----
    "text_primary": "#262521",
    "text_secondary": "#65635e",
    "text_tertiary": "#817f79",
    "text_disabled": "#a09d96",
    "text_on_accent": "#ffffff",
    # ---- borders ----
    "border": "#d7d4ce",
    "border_subtle": "#e8e5df",
    "border_strong": "#c1bdb5",
    # ---- link / focus accent ----
    "accent": "#2c6fbb",
    "accent_hover": "#255e9e",
    "accent_pressed": "#1f508a",
    "accent_disabled": "#a9c4e4",
    # ---- neutral primary action ----
    "action_background": "#292824",
    "action_background_hover": "#3b3933",
    "action_background_pressed": "#1d1c19",
    "action_background_disabled": "#dedbd5",
    "action_text": "#faf9f7",
    "action_text_disabled": "#625f58",
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
    "focus": "#376fa6",
    "overlay": "rgba(0, 0, 0, 0.35)",
}

DARK_COLORS: dict[str, str] = {
    # ---- warm neutral / surfaces ----
    "background": "#1b1a18",
    "surface": "#232220",
    "surface_alt": "#282724",
    "surface_hover": "#34322e",
    "surface_pressed": "#3c3a35",
    "surface_selected": "#302e2a",
    # ---- text ----
    "text_primary": "#f1efeb",
    "text_secondary": "#c3c0b9",
    "text_tertiary": "#99968f",
    "text_disabled": "#74716a",
    "text_on_accent": "#ffffff",
    # ---- borders ----
    "border": "#3d3b36",
    "border_subtle": "#302f2b",
    "border_strong": "#514f49",
    # ---- link / focus accent ----
    "accent": "#91b5d2",
    "accent_hover": "#a9c5dc",
    "accent_pressed": "#779ebd",
    "accent_disabled": "#4a5963",
    # ---- neutral primary action ----
    "action_background": "#e9e6df",
    "action_background_hover": "#f5f2ec",
    "action_background_pressed": "#d4d0c8",
    "action_background_disabled": "#3d3b36",
    "action_text": "#22211e",
    "action_text_disabled": "#aaa7a0",
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
    "focus": "#91b5d2",
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
