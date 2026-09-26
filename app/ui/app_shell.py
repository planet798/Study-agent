"""AppShell：应用外壳（左侧 Sidebar + Workspace: PageHeader + StackedWidget）。

PageKey / PageSpec 提供轻量 page descriptor，避免 MainWindow 继续维护五套
nav_* 手工逻辑。AppShell 本身不含业务；它只负责布局与转发导航请求。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from .components.navigation import SANavigationSidebar, key_value
from .components.page_header import SAPageHeader
from .design import icons as _icons


class PageKey(str, Enum):
    TODAY = "today"
    ROUTES = "routes"
    PRACTICE = "practice"
    SETTINGS = "settings"


@dataclass(frozen=True)
class PageSpec:
    key: str
    title: str
    subtitle: str
    icon: object
    footer: bool = False


# 主导航顺序固定：Today → Routes → Practice →（footer）Settings。
PAGE_SPECS: tuple[PageSpec, ...] = (
    PageSpec(PageKey.TODAY, "今日", "今天的学习计划", _icons.IconName.HOME),
    PageSpec(
        PageKey.ROUTES, "学习路线", "管理学习路线与能力进度", _icons.IconName.BOOK
    ),
    PageSpec(
        PageKey.PRACTICE, "实践项目", "项目与实践证据", _icons.IconName.PROJECT
    ),
    PageSpec(
        PageKey.SETTINGS, "设置", "模型、Prompt 与外观设置",
        _icons.IconName.SETTINGS, footer=True,
    ),
)

PAGE_SPECS_BY_KEY: dict[str, PageSpec] = {key_value(s.key): s for s in PAGE_SPECS}


class AppShell(QWidget):
    """应用外壳。"""

    page_requested = Signal(str)

    def __init__(self, specs=PAGE_SPECS, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AppShell")
        self._specs = tuple(specs)

        self.sidebar = SANavigationSidebar(self._specs, self)
        self.page_header = SAPageHeader()
        self.stack = QStackedWidget()

        workspace = QWidget()
        workspace.setObjectName("SAWorkspace")
        workspace.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        wl = QVBoxLayout(workspace)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(0)
        wl.addWidget(self.page_header)
        wl.addWidget(self.stack, stretch=1)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.sidebar)
        root.addWidget(workspace, stretch=1)

        self.sidebar.page_requested.connect(self.page_requested)

    # ---------- pages ----------
    def add_page(self, widget: QWidget) -> int:
        self.stack.addWidget(widget)
        return self.stack.count() - 1

    def set_item_available(self, key, available: bool) -> None:
        self.sidebar.set_item_available(key, available)

    def set_page_header(self, key, subtitle: str | None = None) -> None:
        spec = PAGE_SPECS_BY_KEY.get(key_value(key))
        if spec is None:
            return
        self.page_header.set_title(spec.title)
        self.page_header.set_subtitle(
            spec.subtitle if subtitle is None else subtitle
        )
        self.page_header.set_icon(spec.icon)

    def select_page(self, key) -> None:
        self.sidebar.set_current(key)
