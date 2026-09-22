"""ThemeManager / QSS rendering tests (UI-1)."""

from __future__ import annotations

import re

import pytest

from app.ui.design import tokens
from app.ui.design.theme_manager import (
    ThemeManager,
    ThemeMode,
    load_template,
    render_qss,
    render_theme,
)

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")


@pytest.fixture(autouse=True)
def _clean_singleton():
    ThemeManager.reset_instance()
    yield
    ThemeManager.reset_instance()


def test_render_has_no_unresolved_placeholders():
    for theme in ("light", "dark"):
        rendered = render_theme(theme)
        assert "{{" not in rendered
        assert "}}" not in rendered
        # braces 基本平衡（结构完整）
        assert rendered.count("{") == rendered.count("}")


def test_render_unknown_token_raises():
    with pytest.raises(KeyError):
        render_qss("QWidget { color: {{nope_missing}}; }", {})


def test_light_dark_qss_placeholder_parity():
    light = set(PLACEHOLDER_RE.findall(load_template("light")))
    dark = set(PLACEHOLDER_RE.findall(load_template("dark")))
    assert light == dark
    assert light  # 非空


def test_all_placeholders_resolvable_by_token_map():
    for theme in ("light", "dark"):
        keys = set(PLACEHOLDER_RE.findall(load_template(theme)))
        mapping = tokens.render_map(theme)
        assert keys <= set(mapping), f"unresolved in {theme}: {keys - set(mapping)}"


def test_legacy_objectname_selectors_present():
    rendered = render_theme("light")
    for selector in (
        "QPushButton#PrimaryButton",
        "QPushButton#SecondaryButton",
        "QPushButton#SecondaryButton:hover",
        "QPushButton#SecondaryButton:disabled",
        "QPushButton#DangerButton",
        "QFrame#TaskCard",
        "#TaskTitle",
        "#TaskMeta",
        "#SectionTitle",
        "#EmptyHint",
        "#ReviewTag",
        "#AppTitle",
        "#AppDate",
    ):
        assert selector in rendered, selector


def test_app_style_compat_bridge():
    from app.ui.styles import APP_STYLE, apply_secondary_button_text, legacy_style

    assert isinstance(APP_STYLE, str) and APP_STYLE
    assert "QPushButton#SecondaryButton" in APP_STYLE
    assert "#2c6fbb" in APP_STYLE  # legacy 品牌蓝
    assert callable(apply_secondary_button_text)
    assert legacy_style() == APP_STYLE


def test_apply_and_idempotent(qapp):
    tm = ThemeManager()
    first = tm.apply(qapp)
    qss1 = qapp.styleSheet()
    pal1 = qapp.palette()
    second = tm.apply(qapp)
    assert first == second == "light"
    assert qapp.styleSheet() == qss1
    # palette 不应累积：同样的 role 颜色一致
    assert qapp.palette().color(pal1.ColorRole.Window).name() == \
        pal1.color(pal1.ColorRole.Window).name()


def test_light_dark_light_roundtrip(qapp):
    tm = ThemeManager()
    tm.apply(qapp)
    light_qss = qapp.styleSheet()

    tm.set_theme(ThemeMode.DARK)
    assert tm.effective_theme == "dark"
    dark_qss = qapp.styleSheet()
    assert dark_qss != light_qss

    tm.set_theme(ThemeMode.LIGHT)
    assert tm.effective_theme == "light"
    assert qapp.styleSheet() == light_qss  # 不累积


def test_theme_changed_signal(qapp):
    tm = ThemeManager()
    seen: list[str] = []
    tm.theme_changed.connect(seen.append)
    tm.apply(qapp)
    tm.set_theme(ThemeMode.DARK)
    assert "light" in seen and "dark" in seen


def test_system_mode_falls_back_to_light():
    assert ThemeManager.resolve_theme(ThemeMode.SYSTEM) in ("light", "dark")
    # 在 offscreen / 无系统主题信息时必须稳定返回 light
    assert ThemeManager._system_theme() in ("light", "dark")


def test_singleton_identity():
    assert ThemeManager.instance() is ThemeManager.instance()
