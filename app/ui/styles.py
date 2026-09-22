"""全局 QSS 兼容桥（legacy）。

历史角色（保留，兼容旧页面与测试）：
- ``APP_STYLE``：旧代码直接 ``app.setStyleSheet(APP_STYLE)`` 使用的字符串。
  现在**代理到 Light theme 渲染结果**（由 Design System 生成），legacy
  objectName selectors（PrimaryButton / SecondaryButton / TaskCard ...）继续生效。
- ``apply_secondary_button_text``：旧调用点使用的 palette 兜底；现在委托给
  ``SAButton`` 内部的同一实现。新组件不得再调用它。

新代码请使用 ``app/ui/design/theme_manager.ThemeManager`` 与
``app/ui/components/``。
"""

from __future__ import annotations

from .components.button import apply_button_text_palette
from .design.theme_manager import render_theme

# 兼容旧常量（palette 兜底颜色）。
SECONDARY_TEXT_COLOR = "#2c6fbb"


def legacy_style() -> str:
    """返回 Light theme 渲染后的 QSS（legacy 视图兼容）。"""
    return render_theme("light")


# APP_STYLE 暂时代理到 Light theme；旧页面视觉不崩，objectName 继续有效。
APP_STYLE = legacy_style()


def apply_secondary_button_text(button) -> None:
    """把“完成 / 打开链接”等次级按钮文字强制为蓝字（legacy helper）。

    Windows 原生 QPushButton 样式可能不采纳 QSS 的 color 属性；这里显式设置
    palette 的 ButtonText。只影响文字颜色，不改尺寸 / 边框 / 布局。
    """
    apply_button_text_palette(button, SECONDARY_TEXT_COLOR)
