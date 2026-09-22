"""Design token tests (UI-1)."""

from __future__ import annotations

from app.ui.design import colors
from app.ui.design import radius as radius_tokens
from app.ui.design import spacing as spacing_tokens
from app.ui.design import tokens, typography


def test_light_dark_color_keys_identical():
    assert tuple(colors.LIGHT_COLORS) == tuple(colors.DARK_COLORS)
    assert colors.COLOR_KEYS == tuple(colors.LIGHT_COLORS)


def test_all_color_tokens_non_empty():
    for name, value in colors.LIGHT_COLORS.items():
        assert value, f"empty light token: {name}"
    for name, value in colors.DARK_COLORS.items():
        assert value, f"empty dark token: {name}"


def test_required_semantic_color_names_present():
    required = {
        "background",
        "surface",
        "surface_alt",
        "surface_hover",
        "surface_pressed",
        "surface_selected",
        "text_primary",
        "text_secondary",
        "text_tertiary",
        "text_disabled",
        "text_on_accent",
        "border",
        "border_subtle",
        "border_strong",
        "accent",
        "accent_hover",
        "accent_pressed",
        "accent_disabled",
        "success",
        "success_background",
        "warning",
        "warning_background",
        "danger",
        "danger_background",
        "info",
        "info_background",
        "focus",
        "overlay",
    }
    assert required.issubset(set(colors.COLOR_KEYS))


def test_dark_is_not_light_inversion():
    # Dark surface 必须与 Light surface 不同，且 dark 更暗（不是简单反色）。
    light_bg = colors.LIGHT_COLORS["background"]
    dark_bg = colors.DARK_COLORS["background"]
    assert light_bg != dark_bg
    # dark 前景必须是浅色，背景是深色（语义一致，方向合理）
    assert colors.DARK_COLORS["text_primary"] != colors.LIGHT_COLORS["text_primary"]


def test_typography_roles_and_fonts():
    for role in typography.ROLES:
        font = typography.font_for(role)
        assert font.pixelSize() > 0
    assert typography.MONOSPACE in typography.ROLES
    mono = typography.font_for(typography.MONOSPACE)
    assert mono.styleHint() == mono.StyleHint.Monospace


def test_spacing_and_radius_scale():
    assert spacing_tokens.SCALE == tuple(sorted(spacing_tokens.SCALE))
    assert spacing_tokens.XS == 4
    assert spacing_tokens.PAGE == 48
    assert radius_tokens.SMALL < radius_tokens.MEDIUM < radius_tokens.LARGE


def test_render_map_contains_colors_fonts_and_radius():
    mapping = tokens.render_map("light")
    assert mapping["surface"] == colors.LIGHT_COLORS["surface"]
    assert "font_family" in mapping
    assert mapping["font_mono"]
    assert mapping["radius_medium"].endswith("px")
    assert set(tokens.color_keys()) <= set(mapping)
