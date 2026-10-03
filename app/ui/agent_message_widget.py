"""Message presentation widgets for the task-bound Agent Workspace (UX-1).

This module is **presentation only**. It has no dependency on any Service,
Repository, Agent Runtime or Tool Registry, and it never mutates persistence:

    AgentWorkspacePage
        └── AgentMessageWidget
                ├── AgentMessageSpeaker  (QLabel)
                ├── AgentUserMessageText (QTextBrowser, PlainText) role=user
                └── AgentAssistantMarkdown (AgentMarkdownView) role=assistant

Design boundaries:
- User input is always rendered as PlainText. Markdown syntax typed by the user
  (``**hello**``) stays literal.
- Assistant output is untrusted model text and is rendered with the Qt-native
  ``QTextDocument`` Markdown importer only. Raw HTML is disabled
  (``MarkdownNoHTML``); no WebEngine / JavaScript / third-party renderer is used.
- Remote images, ``file://`` / ``qrc://`` and any other resource are never
  fetched: :meth:`AgentMarkdownView.loadResource` is a hard no-op and external
  links are never opened.
- The database keeps the raw Markdown string; the rendered document is never
  persisted.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextFrameFormat,
    QTextOption,
    QTextLength,
    QTextTable,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .design import spacing, typography
from .design.theme_manager import theme_manager

USER_ROLE = "user"
ASSISTANT_ROLE = "assistant"

USER_SPEAKER = "你"
ASSISTANT_SPEAKER = "学习助手"

# Assistant body may breathe wider than the user's own messages.
ASSISTANT_MAX_WIDTH = 800
USER_MAX_WIDTH = 680
ASSISTANT_VIEWPORT_FRACTION = 1.0
USER_VIEWPORT_FRACTION = 0.78
# Ceil fractional document height, then retain two logical pixels for descenders.
BODY_HEIGHT_SLACK = 2

# Qt-native Markdown with raw HTML disabled. ``MarkdownDialectGitHub`` enables the
# common GFM subset (tables, fenced code, task lists). ``MarkdownNoHTML`` keeps
# every ``<tag>`` in untrusted model output as literal text.
MARKDOWN_FEATURES = (
    QTextDocument.MarkdownFeature.MarkdownNoHTML
    | QTextDocument.MarkdownFeature.MarkdownDialectGitHub
)

_MONO_HINTS = ("mono", "consol", "cascadia", "courier", "menlo", "fixed")

# Heading levels → typography role (px). Kept small on purpose: a learning
# conversation is not a blog page.
_HEADING_ROLES = {
    1: typography.TITLE_LARGE,
    2: typography.TITLE,
    3: typography.SUBTITLE,
}
_HEADING_TOP_MARGIN = {1: 12, 2: 10, 3: 8}
_HEADING_BOTTOM_MARGIN = 4
_LINE_HEIGHT_PERCENT = 140
_CODE_BLOCK_MARGIN = 10
_CODE_BLOCK_PADDING = 6
_TABLE_CELL_PADDING = 4
_TABLE_BORDER_WIDTH = 0.5


def _base_body_font() -> QFont:
    """Body font for the document default.

    Uses a point size on purpose: Qt's Markdown importer drops paragraph margins
    entirely when the document default font is pixel-sized before import.
    """
    font = typography.font_for(typography.BODY)
    font.setPointSizeF(typography.size_for(typography.BODY) * 0.75)
    return font


def _fragment_is_monospace(char_format: QTextCharFormat) -> bool:
    families = char_format.fontFamilies() or []
    for family in families:
        text = str(family).lower()
        if any(hint in text for hint in _MONO_HINTS):
            return True
    return bool(char_format.fontFixedPitch())


def _is_code_block(block) -> bool:
    """A block is a fenced code block when all of its text is monospace."""
    if not block.text().strip():
        return False
    iterator = block.begin()
    saw_text = False
    while not iterator.atEnd():
        fragment = iterator.fragment()
        if fragment.isValid() and fragment.text():
            saw_text = True
            if not _fragment_is_monospace(fragment.charFormat()):
                return False
        iterator += 1
    return saw_text


class _MessageBodyView(QTextBrowser):
    """Shared read-only body geometry for one message.

    The whole Workspace keeps a **single** outer scroll area; this view never
    shows its own scrollbars, never opens links, never fetches resources and
    always resizes to fit its document. Long unbroken tokens wrap instead of
    forcing the conversation wider than the viewport.
    """

    def __init__(self, object_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setReadOnly(True)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setSearchPaths([])
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.viewport().setAutoFillBackground(False)

        document = self.document()
        document.setDocumentMargin(0.0)
        document.setDefaultFont(_base_body_font())
        document.documentLayout().documentSizeChanged.connect(self._sync_height)
        self._sync_height()

    def loadResource(self, resource_type, url):  # noqa: N802 - Qt API
        """Never fetch anything (http, file, qrc...) for untrusted content."""
        return None

    def _sync_height(self, *_args) -> None:
        # Qt returns a fractional QSizeF; flooring it clips descenders on Windows
        # DPI scales. The slack covers viewport/document rounding and rich blocks.
        margins = self.contentsMargins()
        # Unlike height - viewport.height(), these are stable during resize callbacks.
        chrome = margins.top() + margins.bottom()
        required = math.ceil(self.document().size().height()) + BODY_HEIGHT_SLACK + chrome
        height = max(BODY_HEIGHT_SLACK + chrome, required)
        if self.height() != height:
            self.setFixedHeight(height)

    def _schedule_height(self) -> None:
        QTimer.singleShot(0, self, self._sync_height)

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._sync_height()
        if event.size().width() != event.oldSize().width():
            self._schedule_height()


class AgentPlainTextView(_MessageBodyView):
    """Read-only PlainText view for user input. Markdown stays literal."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("AgentUserMessageText", parent)
        self._text = ""

    @property
    def plain_text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text if isinstance(text, str) else str(text or "")
        self.document().setPlainText(self._text)
        self._sync_height()
        self._schedule_height()
        self.updateGeometry()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        """Shrink to the user's text instead of QTextBrowser's 256px default."""
        metrics = QFontMetrics(self.document().defaultFont())
        lines = self._text.splitlines() or [""]
        natural = max((metrics.horizontalAdvance(line) for line in lines), default=0)
        width = max(1, min(natural + 8, USER_MAX_WIDTH))
        height = self.document().size().height()
        if height <= 0:
            height = float(metrics.height() * max(1, len(lines)))
        return QSize(width, math.ceil(height) + BODY_HEIGHT_SLACK)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(1, self.height())


class AgentMarkdownView(_MessageBodyView):
    """Read-only, safe Markdown view for one assistant message."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("AgentAssistantMarkdown", parent)
        self._markdown = ""
        self._plaintext_fallback = False
        self._table_timer = QTimer(self)
        self._table_timer.setSingleShot(True)
        self._table_timer.timeout.connect(self._resize_tables)
        self._theme_manager = theme_manager()
        self._theme_manager.theme_changed.connect(self._on_theme_changed)

    # ---------- public API (markdown str in, no business objects) ----------

    @property
    def markdown(self) -> str:
        return self._markdown

    @property
    def using_plaintext_fallback(self) -> bool:
        return self._plaintext_fallback

    def set_markdown(self, markdown: str) -> None:
        self._markdown = markdown if isinstance(markdown, str) else str(markdown or "")
        self._render()

    # ---------- rendering ----------

    def _render(self) -> None:
        document = self.document()
        # The default font is applied *before* Markdown import with a point size:
        # a pixel-sized default font makes the importer drop paragraph margins.
        document.setDefaultFont(_base_body_font())
        try:
            document.setMarkdown(self._markdown, MARKDOWN_FEATURES)
            self._normalize_table_leading_blocks(document.rootFrame())
            self._plaintext_fallback = False
            self._style_document()
        except Exception:  # noqa: BLE001 - one bad model output must not crash the UI
            document.setPlainText(self._markdown)
            self._plaintext_fallback = True
        self._sync_height()
        self._schedule_height()

    def _theme_tokens(self) -> dict[str, str]:
        try:
            return self._theme_manager.tokens()
        except Exception:  # noqa: BLE001 - uninitialised theme falls back to plain
            return {}

    def _style_document(self) -> None:
        if self._plaintext_fallback:
            return
        tokens = self._theme_tokens()
        code_background = self._color(tokens, "surface_alt")
        text_primary = self._color(tokens, "text_primary")
        document = self.document()

        # Two passes: classify first, then style by re-fetching each block from
        # the document. Mutating block formats with a QTextCursor invalidates
        # QTextBlock iterators, so the classification must not interleave with
        # the mutations.
        plan: list[tuple[int, int, bool]] = []
        in_code = False
        block = document.begin()
        while block.isValid():
            is_code = _is_code_block(block)
            if is_code:
                in_code = True
            elif block.text().strip() or QTextCursor(block).currentTable() is not None:
                in_code = False
            # Blank lines inside a fenced block keep the code-block surface.
            is_code = is_code or (in_code and not block.text().strip())
            plan.append((block.blockNumber(), block.blockFormat().headingLevel(), is_code))
            block = block.next()

        for number, heading_level, is_code in plan:
            block = document.findBlockByNumber(number)
            if not block.isValid():
                continue
            if heading_level:
                self._style_heading(block, heading_level, text_primary)
            if is_code:
                self._style_code_block(block, code_background)
            elif not heading_level:
                self._style_paragraph(block)
            self._style_inline_code(block, code_background)

        self._style_tables(document.rootFrame(), tokens)

    def _normalize_table_leading_blocks(self, frame) -> None:
        for child in frame.childFrames():
            if isinstance(child, QTextTable):
                cell = child.cellAt(0, 0)
                first = cell.firstCursorPosition().block()
                following = first.next()
                # Qt's importer can put a spurious empty paragraph in the first
                # header cell after a code fence. Remove only that generated block.
                if (not first.text() and following.isValid()
                        and following.position() < cell.lastCursorPosition().position()):
                    cursor = QTextCursor(first)
                    cursor.deleteChar()
                    # The same importer quirk leaves this header without the
                    # bold format applied to the other GFM header cells.
                    cursor = cell.firstCursorPosition()
                    cursor.setPosition(cell.lastCursorPosition().position(), QTextCursor.MoveMode.KeepAnchor)
                    header_format = QTextCharFormat()
                    header_format.setFontWeight(QFont.Weight.Bold)
                    cursor.mergeCharFormat(header_format)
            self._normalize_table_leading_blocks(child)

    def _style_tables(self, frame, tokens: dict[str, str]) -> None:
        for child in frame.childFrames():
            if isinstance(child, QTextTable):
                fmt = child.format()
                # Qt rounds each column separately and includes cell/border gutters
                # outside its advertised table width. Reserve that chrome, not text.
                chrome = 2 * (_TABLE_CELL_PADDING + math.ceil(_TABLE_BORDER_WIDTH))
                width = max(1, self.viewport().width() - chrome - child.columns())
                fmt.setWidth(QTextLength(QTextLength.Type.FixedLength, width))
                fmt.setColumnWidthConstraints([
                    QTextLength(QTextLength.Type.PercentageLength, 100 / child.columns())
                    for _ in range(child.columns())
                ])
                fmt.setCellPadding(_TABLE_CELL_PADDING)
                fmt.setCellSpacing(0)
                fmt.setBorder(_TABLE_BORDER_WIDTH)
                fmt.setBorderCollapse(True)
                fmt.setBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
                border = self._color(tokens, "border_subtle")
                if border is not None:
                    fmt.setBorderBrush(QBrush(border))
                if fmt != child.format():
                    child.setFormat(fmt)
                for row in range(child.rows()):
                    for column in range(child.columns()):
                        cell = child.cellAt(row, column)
                        cell_format = cell.format().toTableCellFormat()
                        cell_format.setBorder(_TABLE_BORDER_WIDTH)
                        cell_format.setBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
                        if border is not None:
                            cell_format.setBorderBrush(QBrush(border))
                        if cell_format != cell.format():
                            cell.setFormat(cell_format)
            self._style_tables(child, tokens)

    def _resize_tables(self) -> None:
        self._style_tables(self.document().rootFrame(), self._theme_tokens())
        self._sync_height()

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._table_timer.start(0)

    @staticmethod
    def _color(tokens: dict[str, str], key: str) -> QColor | None:
        value = tokens.get(key)
        if not value:
            return None
        color = QColor(value)
        return color if color.isValid() else None

    @staticmethod
    def _mono_fragment_ranges(block) -> list[tuple[int, int]]:
        """Collect monospace fragment ranges before any document mutation."""
        ranges: list[tuple[int, int]] = []
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if (
                fragment.isValid()
                and fragment.text()
                and _fragment_is_monospace(fragment.charFormat())
            ):
                ranges.append((fragment.position(), fragment.length()))
            iterator += 1
        return ranges

    @staticmethod
    def _all_fragment_ranges(block) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and fragment.text():
                ranges.append((fragment.position(), fragment.length()))
            iterator += 1
        return ranges

    def _style_heading(self, block, level: int, text_primary: QColor | None) -> None:
        role = _HEADING_ROLES.get(level, typography.SUBTITLE)
        position = block.position()
        text_length = len(block.text())
        block_format = QTextBlockFormat(block.blockFormat())
        # Heading blocks in Qt hard-code an ``x-large`` size; neutralise the level
        # and control size ourselves so headings stay close to body text.
        block_format.setHeadingLevel(0)
        block_format.setTopMargin(0 if position == 0 else _HEADING_TOP_MARGIN.get(level, 8))
        block_format.setBottomMargin(_HEADING_BOTTOM_MARGIN)
        cursor = QTextCursor(self.document())
        cursor.setPosition(position)
        cursor.setBlockFormat(block_format)

        char_format = QTextCharFormat()
        cursor.setPosition(position)
        cursor.setPosition(position + text_length, QTextCursor.MoveMode.KeepAnchor)
        char_format.setProperty(QTextFormat.Property.FontSizeAdjustment, 0)
        char_format.setFontPointSize(typography.size_for(role) * 0.75)
        char_format.setFontWeight(typography.font_for(role).weight())
        if text_primary is not None:
            char_format.setForeground(QBrush(text_primary))
        cursor.mergeCharFormat(char_format)

    def _style_paragraph(self, block) -> None:
        block_format = QTextBlockFormat(block.blockFormat())
        block_format.setLineHeight(
            float(_LINE_HEIGHT_PERCENT),
            QTextBlockFormat.LineHeightTypes.ProportionalHeight.value,
        )
        block_format.setTopMargin(0)
        block_format.setBottomMargin(4 if block.textList() else 8)
        if not block.next().isValid():
            block_format.setBottomMargin(0)
        cursor = QTextCursor(self.document())
        cursor.setPosition(block.position())
        cursor.setBlockFormat(block_format)

    def _style_code_block(self, block, background: QColor | None) -> None:
        position = block.position()
        ranges = self._all_fragment_ranges(block)
        for start, length in ranges:
            cursor = QTextCursor(self.document())
            cursor.setPosition(start)
            cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
            char_format = QTextCharFormat()
            self._apply_mono_format(char_format)
            if background is not None:
                char_format.setBackground(QBrush(background))
            cursor.mergeCharFormat(char_format)

        block_format = QTextBlockFormat(self.document().findBlock(position).blockFormat())
        if background is not None:
            block_format.setBackground(background)
        block_format.setLeftMargin(_CODE_BLOCK_MARGIN)
        block_format.setRightMargin(_CODE_BLOCK_MARGIN)
        block_format.setNonBreakableLines(False)
        block_format.setTopMargin(_CODE_BLOCK_PADDING)
        block_format.setBottomMargin(_CODE_BLOCK_PADDING)
        cursor = QTextCursor(self.document())
        cursor.setPosition(position)
        cursor.setBlockFormat(block_format)

    def _style_inline_code(self, block, background: QColor | None) -> None:
        ranges = self._mono_fragment_ranges(block)
        for start, length in ranges:
            cursor = QTextCursor(self.document())
            cursor.setPosition(start)
            cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
            char_format = QTextCharFormat()
            self._apply_mono_format(char_format)
            if background is not None:
                char_format.setBackground(QBrush(background))
            cursor.mergeCharFormat(char_format)

    @staticmethod
    def _apply_mono_format(char_format: QTextCharFormat) -> None:
        char_format.setFontFamilies(["Cascadia Mono", "Consolas", "monospace"])
        char_format.setFontFixedPitch(True)
        char_format.setProperty(QTextFormat.Property.FontSizeAdjustment, 0)
        char_format.setFontPointSize(typography.size_for(typography.MONOSPACE) * 0.75)

    # ---------- theme ----------

    def _on_theme_changed(self, *_args) -> None:
        self._render()


class AgentMessageWidget(QWidget):
    """One conversation row: speaker label + role-specific body."""

    def __init__(
        self,
        role: str,
        text: str,
        speaker: str | None = None,
        parent: QWidget | None = None,
        message_id: int | None = None,
    ):
        super().__init__(parent)
        if role not in (USER_ROLE, ASSISTANT_ROLE):
            raise ValueError(f"unknown agent message role: {role!r}")
        self.role = role
        self.message_id = message_id
        self.raw_text = text if isinstance(text, str) else str(text or "")
        self.speaker = speaker or (
            USER_SPEAKER if role == USER_ROLE else ASSISTANT_SPEAKER
        )
        self.markdown_view: AgentMarkdownView | None = None
        self.plain_view: AgentPlainTextView | None = None

        self.setObjectName("AgentMessageRow")
        self.setProperty("role", self.role)
        self.setAccessibleName(self.speaker)
        self._build_ui()

    @property
    def text(self) -> str:
        """Original stored text (never the rendered form)."""
        return self.raw_text

    def _build_ui(self) -> None:
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.bubble = QFrame()
        self.bubble.setObjectName("AgentMessageBubble")
        self.bubble.setProperty("role", self.role)
        self.bubble.setMaximumWidth(
            USER_MAX_WIDTH if self.role == USER_ROLE else ASSISTANT_MAX_WIDTH
        )
        self.bubble.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )

        bubble_layout = QVBoxLayout(self.bubble)
        if self.role == USER_ROLE:
            bubble_layout.setContentsMargins(spacing.MD, spacing.SM, spacing.MD, spacing.SM)
        else:
            bubble_layout.setContentsMargins(0, 0, 0, 0)
        bubble_layout.setSpacing(spacing.XS)

        self.speaker_label = QLabel(self.speaker)
        self.speaker_label.setObjectName("AgentMessageSpeaker")
        self.speaker_label.setTextFormat(Qt.TextFormat.PlainText)
        bubble_layout.addWidget(self.speaker_label)
        self.speaker_label.setVisible(self.role != USER_ROLE)

        if self.role == USER_ROLE:
            self.plain_view = AgentPlainTextView()
            self.plain_view.set_text(self.raw_text)
            bubble_layout.addWidget(self.plain_view)
            row.addStretch(1)
            row.addWidget(self.bubble)
        else:
            self.markdown_view = AgentMarkdownView()
            self.markdown_view.set_markdown(self.raw_text)
            bubble_layout.addWidget(self.markdown_view)
            # Assistant answers expand to the available width, capped by the
            # bubble's maximum width.
            row.addWidget(self.bubble, 1)
            row.addStretch(0)

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        super().resizeEvent(event)
        cap = USER_MAX_WIDTH if self.role == USER_ROLE else ASSISTANT_MAX_WIDTH
        fraction = (USER_VIEWPORT_FRACTION if self.role == USER_ROLE
                    else ASSISTANT_VIEWPORT_FRACTION)
        available = min(cap, max(1, int(self.width() * fraction)))
        if self.plain_view is not None:
            margins = self.bubble.layout().contentsMargins()
            natural = self.plain_view.sizeHint().width() + margins.left() + margins.right()
            # A fixed minimum would pin the old width across narrow resizes.
            self.bubble.setMaximumWidth(min(available, natural))
        else:
            self.bubble.setMaximumWidth(available)
