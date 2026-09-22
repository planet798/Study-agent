"""Study-Agent Design System (Fluent 2 foundation).

只提供 token、主题、QSS、图标与基础组件；不包含任何业务逻辑。
业务层（页面 / 对话框）只使用 semantic token 与组件，不直接写 hex。
"""

from __future__ import annotations

__all__ = [
    "colors",
    "typography",
    "spacing",
    "radius",
    "shadows",
    "tokens",
    "icons",
    "theme_manager",
]
