"""Development-only theme smoke demo (UI-1).

不属于生产导航，也不被任何 production 代码 import。
用途：在 offscreen 环境下快速验证 Light / Dark token 与 foundation components
能构造并渲染；可选保存开发截图到 --out 目录。

用法::

    QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/theme_smoke_demo.py --out /tmp/sa_ui1
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.ui.components.button import SAButton, SAIconButton  # noqa: E402
from app.ui.components.card import SACard  # noqa: E402
from app.ui.components.empty_state import SAEmptyState  # noqa: E402
from app.ui.components.section_header import SASectionHeader  # noqa: E402
from app.ui.components.status_badge import SAStatusBadge  # noqa: E402
from app.ui.components.tag import SATag  # noqa: E402
from app.ui.design import spacing  # noqa: E402
from app.ui.design.icons import IconName  # noqa: E402
from app.ui.design.theme_manager import ThemeManager, ThemeMode  # noqa: E402


def build_gallery() -> QWidget:
    root = QWidget()
    root.setMinimumSize(720, 560)
    layout = QVBoxLayout(root)
    layout.setContentsMargins(spacing.XL, spacing.XL, spacing.XL, spacing.XL)
    layout.setSpacing(spacing.LG)

    layout.addWidget(SASectionHeader("Foundation Components", subtitle="Light / Dark smoke"))
    layout.addWidget(SASectionHeader("Buttons"))

    row = QHBoxLayout()
    row.addWidget(SAButton("Primary", variant="primary"))
    row.addWidget(SAButton("Secondary", variant="secondary"))
    row.addWidget(SAButton("Subtle", variant="subtle"))
    row.addWidget(SAButton("Danger", variant="danger"))
    row.addWidget(SAButton("Small", variant="secondary", size="small"))
    row.addWidget(SAIconButton(IconName.SETTINGS, tooltip="Settings"))
    row.addWidget(SAIconButton(IconName.MORE, tooltip="More"))
    row.addWidget(SAIconButton(IconName.DELETE, variant="danger", tooltip="Delete"))
    row.addStretch()
    layout.addLayout(row)

    card = SACard(variant="interactive")
    card.add_widget(SASectionHeader("SACard", subtitle="surface + subtle border, no shadow"))
    tag_row = QHBoxLayout()
    for variant in ("neutral", "accent", "success", "warning", "danger", "info"):
        tag_row.addWidget(SATag(variant, variant=variant))
    tag_row.addStretch()
    card.add_layout(tag_row)
    status_row = QHBoxLayout()
    for status in ("active", "in_progress", "planned", "paused", "archived", "completed"):
        status_row.addWidget(SAStatusBadge(status))
    status_row.addStretch()
    card.add_layout(status_row)
    layout.addWidget(card)

    inputs = QHBoxLayout()
    inputs.addWidget(QLineEdit("input"))
    combo = QComboBox()
    combo.addItems(["one", "two", "three"])
    inputs.addWidget(combo)
    layout.addLayout(inputs)

    tabs = QTabWidget()
    tabs.addTab(QLabel("tab one"), "One")
    tabs.addTab(QLabel("tab two"), "Two")
    layout.addWidget(tabs)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    inner = QWidget()
    il = QVBoxLayout(inner)
    for i in range(12):
        il.addWidget(QLabel(f"scroll row {i}"))
    scroll.setWidget(inner)
    layout.addWidget(scroll, stretch=1)

    layout.addWidget(
        SAEmptyState(
            title="Nothing here yet",
            description="SAEmptyState with optional action",
            icon_name=IconName.INFO,
            action=SAButton("Create", variant="primary"),
        )
    )
    return root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="", help="directory to save light/dark screenshots")
    args = parser.parse_args()

    app = QApplication([])
    tm = ThemeManager.instance()
    tm.apply(app)
    gallery = build_gallery()
    gallery.show()

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for mode in (ThemeMode.LIGHT, ThemeMode.DARK):
            tm.set_theme(mode)
            app.processEvents()
            path = out / f"foundation_{tm.effective_theme}.png"
            gallery.grab().save(str(path))
            print("saved", path)

    print("light/dark gallery constructed OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
