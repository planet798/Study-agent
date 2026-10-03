"""Native message-embedded document panels; original source is the copy authority."""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .agent_content_blocks import FencedBlock, MarkdownBlock


class _PanelClip(QWidget):
    """Clip a full-height native child, never scroll or truncate its document."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.view = None
        self.expanded = False
        self.overflow = False
        self.changed = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.recalculate)

    def set_view(self, view):
        if self.view is not None:
            self.view.hide()
        self.view = view
        view.installEventFilter(self)
        view.show()
        self.recalculate()
        self.schedule()

    def schedule(self, *_args):
        self._timer.start(0)

    def recalculate(self):
        if self.view is None:
            return
        view = self.view
        view.resize(max(1, self.width()), view.height())
        view._sync_height()
        full = view.height()
        cap = 14 * QFontMetrics(view.document().defaultFont()).lineSpacing() + 2
        self.overflow = full > cap
        if self.overflow and not self.expanded:
            # End the excerpt at a complete native visual line rather than
            # exposing the top half of the next line. Full documents stay intact.
            layout = view.document().documentLayout()
            boundary = 0
            block = view.document().begin()
            checked = 0
            while block.isValid() and checked < 512:
                top = layout.blockBoundingRect(block).top()
                if top > cap:
                    break
                text_layout = block.layout()
                for number in range(text_layout.lineCount()):
                    line = text_layout.lineAt(number)
                    bottom = math.ceil(top + line.y() + line.height())
                    if bottom > cap:
                        break
                    boundary = max(boundary, bottom)
                block = block.next()
                checked += 1
            cap = boundary or cap
        height = full if self.expanded else min(full, cap)
        view.move(0, 0)
        if self.height() != height:
            self.setFixedHeight(height)
        if self.changed:
            self.changed()

    def sizeHint(self):  # noqa: N802
        return QSize(1, self.height())

    def minimumSizeHint(self):  # noqa: N802
        return QSize(1, self.height())

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.schedule()

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() in (QEvent.Type.Resize, QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.schedule()
        return super().eventFilter(watched, event)

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.schedule()
        return result


class AgentCodePanel(QFrame):
    """One fence, optional safe Markdown preview, and lossless one-click copy."""
    content_interaction = Signal()

    def __init__(self, block: FencedBlock, parent=None):
        # Lazy imports keep native views reusable without a circular import.
        from .agent_message_widget import AgentMarkdownView, AgentPlainTextView

        super().__init__(parent)
        self.block = block
        self.setObjectName("AgentCodePanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(8)
        header = QHBoxLayout()
        label = QLabel(block.language or "纯文本")
        label.setObjectName("AgentCodeLanguage")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.addWidget(label, 1)
        self.copy_button = QPushButton("复制")
        self.copy_button.setObjectName("AgentCodeCopy")
        self.copy_button.setAccessibleName("复制完整源码")
        self.copy_button.setToolTip("复制完整原始源码（不含外层围栏）")
        header.addWidget(self.copy_button)
        layout.addLayout(header)
        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.timeout.connect(lambda: self.copy_button.setText("复制"))
        self.copy_button.clicked.connect(self.copy_source)

        self.clip = _PanelClip(self)
        self.source_view = AgentPlainTextView(self.clip)
        self.source_view.setObjectName("AgentCodeSource")
        self.source_view.set_text(block.payload)
        font = self.source_view.document().defaultFont()
        font.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
        font.setFixedPitch(True)
        self.source_view.setFont(font)
        self.source_view.document().setDefaultFont(font)
        self.source_view.document().documentLayout().documentSizeChanged.connect(self.clip.schedule)
        self.preview_view = None
        self.preview_button = None
        self.source_button = None
        if block.language.lower() in ("md", "markdown"):
            tabs = QHBoxLayout()
            self.preview_button = QPushButton("预览")
            self.source_button = QPushButton("源码")
            for button in (self.preview_button, self.source_button):
                button.setObjectName("AgentCodeTab")
                button.setCheckable(True)
                tabs.addWidget(button)
            tabs.addStretch()
            layout.addLayout(tabs)
            self.preview_view = AgentMarkdownView(self.clip, continuous_code=True)
            self.preview_view.set_markdown(block.payload)
            self.preview_view.document().documentLayout().documentSizeChanged.connect(self.clip.schedule)
            self.preview_button.clicked.connect(lambda: self.set_preview(True))
            self.source_button.clicked.connect(lambda: self.set_preview(False))
        layout.addWidget(self.clip)
        self.expand_button = QPushButton("展开全文")
        self.expand_button.setObjectName("AgentCodeExpand")
        self.expand_button.clicked.connect(self.toggle_expanded)
        layout.addWidget(self.expand_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.clip.changed = self._update_expander
        self.source_view.hide()
        self._select_view(self.preview_view is not None)

    def copy_source(self):
        QApplication.clipboard().setText(self.block.payload)
        self.copy_button.setText("已复制")
        self._copy_timer.start(1200)

    def _update_expander(self):
        self.expand_button.setVisible(self.clip.overflow)
        self.expand_button.setText("收起" if self.clip.expanded else "展开全文")

    def _select_view(self, preview: bool):
        view = self.preview_view if preview and self.preview_view is not None else self.source_view
        if self.preview_button is not None:
            self.preview_button.setChecked(view is self.preview_view)
            self.source_button.setChecked(view is self.source_view)
        self.clip.set_view(view)

    def set_preview(self, preview: bool):
        self.content_interaction.emit()
        self._select_view(preview)

    def toggle_expanded(self):
        self.content_interaction.emit()
        self.clip.expanded = not self.clip.expanded
        self.clip.recalculate()


class AgentAssistantContent(QWidget):
    """Ordered native Markdown and fence panels; keeps full raw Markdown API."""
    content_interaction = Signal()

    def __init__(self, markdown: str, blocks: list[MarkdownBlock | FencedBlock], parent=None):
        from .agent_message_widget import AgentMarkdownView

        super().__init__(parent)
        self.setObjectName("AgentAssistantContent")
        self.markdown = markdown
        self.panels: list[AgentCodePanel] = []
        self.views: list[QWidget] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for block in blocks:
            if isinstance(block, FencedBlock):
                view = AgentCodePanel(block)
                view.content_interaction.connect(self.content_interaction)
                self.panels.append(view)
            else:
                if not block.source.strip():
                    continue
                view = AgentMarkdownView()
                view.set_markdown(block.source, reference_context=block.reference_context)
            self.views.append(view)
            layout.addWidget(view)

    def toPlainText(self):  # noqa: N802
        return "\n".join(view.block.payload if isinstance(view, AgentCodePanel)
                         else view.toPlainText() for view in self.views)
