"""Icon system tests (UI-1)."""

from __future__ import annotations

import pytest

from app.ui.design import icons
from app.ui.design.icons import IconName, icon, icon_path, pixmap


def _first_opaque_color(pm):
    img = pm.toImage()
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() > 0:
                return c
    return None


def test_all_icon_names_have_assets():
    for name in IconName:
        path = icon_path(name)
        assert path.exists(), name
        assert path.suffix == ".svg"


def test_every_icon_renders():
    for name in IconName:
        pm = pixmap(name, size=24, color="#000000")
        assert not pm.isNull()
        assert pm.size().width() == 24
        assert icon(name).isNull() is False


def test_pixmap_size_respected():
    pm = pixmap(IconName.ADD, size=16, color="#000000")
    assert pm.size().width() == 16 and pm.size().height() == 16


def test_icon_tint_follows_color():
    red = _first_opaque_color(pixmap(IconName.ADD, size=24, color="#ff0000"))
    blue = _first_opaque_color(pixmap(IconName.ADD, size=24, color="#0000ff"))
    assert red is not None and blue is not None
    assert red.red() > 200 and red.blue() < 50
    assert blue.blue() > 200 and blue.red() < 50


def test_filled_variant_available_and_fallback():
    # home 有 filled 资产
    assert icon_path(IconName.HOME, filled=True).name == "home_filled.svg"
    # add 没有 filled 资产 -> 回退 regular，不应报错
    assert icon_path(IconName.ADD, filled=True).name == "add.svg"


def test_no_per_theme_duplicate_assets():
    names = {p.name for p in icons._ICON_DIR.glob("*.svg")}
    assert not any(n.startswith(("light_", "dark_")) for n in names)


def test_unknown_icon_raises():
    with pytest.raises(ValueError):
        icon_path("definitely_not_an_icon")


def test_icon_cache_clear():
    pixmap(IconName.HOME, size=24, color="#000000")
    icons.clear_cache()
    assert not icons._pixmap_cache
