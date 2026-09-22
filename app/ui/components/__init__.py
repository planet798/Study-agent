"""Foundation components (Fluent 2 design system).

组件只负责视觉与交互，不调用任何 service，不包含业务语义。
所有颜色通过全局 QSS + semantic token 提供，组件内部不写 hex。
"""

from __future__ import annotations

__all__ = [
    "button",
    "card",
    "section_header",
    "tag",
    "status_badge",
    "empty_state",
]
