"""Agent message widget presentation + Markdown safety boundary (UX-1)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QFrame

from app.ui import agent_message_widget as widget_module
from app.ui.design import colors
from app.ui.agent_message_widget import (
    ASSISTANT_MAX_WIDTH,
    ASSISTANT_ROLE,
    ASSISTANT_SPEAKER,
    USER_MAX_WIDTH,
    USER_ROLE,
    USER_SPEAKER,
    AgentMarkdownView,
    AgentMessageWidget,
    AgentPlainTextView,
)

MARKDOWN_EXAMPLE = (
    "## SFT\n\n"
    "SFT 是 **监督微调**。\n\n"
    "- instruction\n"
    "- input\n"
    "- output\n\n"
    "`loss`\n\n"
    "```json\n"
    '{"input":"hello"}\n'
    "```\n"
)

HTML_INJECTION = (
    "<h1>PRIVATE</h1>\n"
    "<script>alert(1)</script>\n"
    "<img src='file:///C:/secret'>\n"
)

MALFORMED = "```python\nunclosed\n\n**broken\n<table><script>alert(1)</script>\n"


@pytest.fixture(autouse=True)
def _restore_theme(qapp):
    yield
    from app.ui.design.theme_manager import ThemeManager
    ThemeManager.instance().set_theme("light")


# ---------------------------------------------------------------- user text


def test_user_message_is_plain_text(qtbot):
    from PySide6.QtCore import Qt
    widget = AgentMessageWidget(USER_ROLE, "**hello** `<script>`")
    qtbot.addWidget(widget)
    assert widget.role == USER_ROLE
    assert widget.speaker == USER_SPEAKER
    assert isinstance(widget.plain_view, AgentPlainTextView)
    assert widget.markdown_view is None
    assert widget.plain_view.toPlainText() == "**hello** `<script>`"
    assert widget.plain_view.plain_text == "**hello** `<script>`"
    assert widget.plain_view.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    # No Markdown document: the literal text is the only content.
    assert widget.plain_view.toPlainText() == widget.raw_text


def test_user_message_is_not_interpreted_as_markdown(qtbot):
    widget = AgentMessageWidget(USER_ROLE, "# not a heading\n\n**not bold**")
    qtbot.addWidget(widget)
    assert widget.plain_view.toPlainText() == "# not a heading\n\n**not bold**"
    assert "# not a heading" in widget.plain_view.toPlainText()


# ---------------------------------------------------------- assistant markdown


def test_assistant_message_uses_safe_markdown(qtbot):
    widget = AgentMessageWidget(ASSISTANT_ROLE, MARKDOWN_EXAMPLE)
    qtbot.addWidget(widget)
    assert widget.role == ASSISTANT_ROLE
    assert widget.speaker == ASSISTANT_SPEAKER
    from app.ui.agent_code_panel import AgentAssistantContent
    assert isinstance(widget.markdown_view, AgentAssistantContent)
    assert len(widget.markdown_view.panels) == 1
    assert widget.plain_view is None
    assert widget.raw_text == MARKDOWN_EXAMPLE  # original persisted string untouched

    plain = widget.markdown_view.toPlainText()
    assert "SFT" in plain and "监督微调" in plain and "loss" in plain
    for marker in ("## ", "**", "```", "`loss`"):
        assert marker not in plain


def test_assistant_markdown_view_keeps_raw_markdown_text(qtbot):
    view = AgentMarkdownView()
    qtbot.addWidget(view)
    view.set_markdown(MARKDOWN_EXAMPLE)
    assert view.markdown == MARKDOWN_EXAMPLE
    assert view.using_plaintext_fallback is False


# ---------------------------------------------------------------- HTML safety


def test_raw_html_is_disabled(qtbot):
    view = AgentMarkdownView()
    qtbot.addWidget(view)
    view.set_markdown(HTML_INJECTION)
    html = view.document().toHtml()
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
    assert "<h1" not in html
    assert "PRIVATE" in view.toPlainText()
    assert "alert(1)" in view.toPlainText()


def test_no_external_links_open(qtbot):
    view = AgentMarkdownView()
    qtbot.addWidget(view)
    assert view.openExternalLinks() is False
    assert view.openLinks() is False
    view.set_markdown("[click](https://example.com/private)")
    assert "click" in view.toPlainText()


# ------------------------------------------------------------- resource guard


def test_load_resource_never_fetches_remote_or_local(qtbot):
    view = AgentMarkdownView()
    qtbot.addWidget(view)
    resource_type = QTextDocument.ResourceType.ImageResource
    for url in (
        "https://example.com/private.png",
        "http://example.com/private.png",
        "file:///C:/secret.png",
        "qrc:/secret.png",
        "/etc/shadow",
    ):
        assert view.loadResource(resource_type, QUrl(url)) is None


def test_remote_image_is_not_loaded(qtbot):
    view = AgentMarkdownView()
    qtbot.addWidget(view)
    view.set_markdown("![x](https://example.com/private.png)")
    resource = view.document().resource(
        QTextDocument.ResourceType.ImageResource,
        QUrl("https://example.com/private.png"),
    )
    assert resource is None or not getattr(resource, "isValid", lambda: True)()


def test_malformed_markdown_does_not_crash(qtbot):
    view = AgentMarkdownView()
    qtbot.addWidget(view)
    view.set_markdown(MALFORMED)
    assert view.document() is not None
    assert view.toPlainText()
    assert "<script>" not in view.document().toHtml()


def test_renderer_falls_back_to_plaintext_on_error(qtbot, monkeypatch):
    view = AgentMarkdownView()
    qtbot.addWidget(view)
    monkeypatch.setattr(widget_module, "MARKDOWN_FEATURES", object())
    view.set_markdown("# Title")
    assert view.using_plaintext_fallback is True
    assert "# Title" in view.toPlainText()


# --------------------------------------------------------------- layout / roles


def test_roles_have_distinct_properties_and_alignment(qtbot):
    user = AgentMessageWidget(USER_ROLE, "question")
    assistant = AgentMessageWidget(ASSISTANT_ROLE, "answer")
    qtbot.addWidget(user)
    qtbot.addWidget(assistant)

    assert user.objectName() == "AgentMessageRow"
    assert user.property("role") == "user"
    assert user.bubble.objectName() == "AgentMessageBubble"
    assert user.bubble.property("role") == "user"
    assert assistant.property("role") == "assistant"
    assert assistant.bubble.property("role") == "assistant"

    user_row = user.layout()
    assert user_row.itemAt(0).spacerItem() is not None
    assert user_row.itemAt(user_row.count() - 1).widget() is user.bubble
    assistant_row = assistant.layout()
    assert assistant_row.itemAt(0).widget() is assistant.bubble
    assert assistant_row.itemAt(assistant_row.count() - 1).spacerItem() is not None


def test_user_and_assistant_bubbles_have_max_width(qtbot):
    user = AgentMessageWidget(USER_ROLE, "question")
    assistant = AgentMessageWidget(ASSISTANT_ROLE, "answer")
    qtbot.addWidget(user)
    qtbot.addWidget(assistant)
    assert user.bubble.maximumWidth() == USER_MAX_WIDTH
    assert assistant.bubble.maximumWidth() == ASSISTANT_MAX_WIDTH
    assert USER_MAX_WIDTH < ASSISTANT_MAX_WIDTH


def test_bubble_shrinks_responsively_in_narrow_viewport(qtbot):
    from app.ui.agent_workspace_page import AgentWorkspacePage
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.resize(360, 480)
    page.show()
    page._add_bubble(ASSISTANT_SPEAKER, "a" * 400, ASSISTANT_ROLE)
    qtbot.waitExposed(page)
    widget = page.findChildren(AgentMessageWidget)[0]
    viewport_width = page.conversation_scroll.viewport().width()
    qtbot.waitUntil(lambda: 0 < widget.bubble.width() <= viewport_width)
    assert widget.bubble.width() <= viewport_width


def test_markdown_view_has_no_internal_scrollbars(qtbot):
    view = AgentMarkdownView()
    qtbot.addWidget(view)
    from PySide6.QtCore import Qt
    assert view.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert view.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert view.frameShape() == QFrame.Shape.NoFrame


def test_markdown_view_height_follows_document(qtbot):
    view = AgentMarkdownView()
    qtbot.addWidget(view)
    view.resize(600, 100)
    view.show()
    qtbot.waitExposed(view)
    view.set_markdown("short")
    short_height = view.height()
    view.set_markdown("\n\n".join(f"paragraph {i}" for i in range(40)))
    assert view.height() > short_height


# -------------------------------------------------------- workspace integration


def test_workspace_uses_distinct_widgets_preserving_order(qtbot, repo):
    from app.ui.agent_workspace_page import AgentWorkspacePage

    task = repo.create(
        title="T", scheduled_date="2026-01-05", estimated_minutes=20,
        source="generated",
    )
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session(
        {"id": 1},
        [
            {"role": "user", "content": "u1", "tool_calls_json": ""},
            {"role": "assistant", "content": "a1", "tool_calls_json": ""},
            {"role": "user", "content": "u2", "tool_calls_json": ""},
        ],
        task, None, model_configured=True,
    )
    widgets = page.findChildren(AgentMessageWidget)
    assert [w.role for w in widgets] == [USER_ROLE, ASSISTANT_ROLE, USER_ROLE]
    assert [w.raw_text for w in widgets] == ["u1", "a1", "u2"]

    page.load_session({"id": 2}, [], task, None, model_configured=True)
    assert page.findChildren(AgentMessageWidget) == []


def test_rendering_does_not_mutate_persisted_messages(
    qtbot, conn, repo, task_service
):
    from app.agent.session import AgentSessionService
    from app.database.agent_repository import AgentRepository
    from app.ui.agent_workspace_page import AgentWorkspacePage

    task = repo.create(title="T", scheduled_date="2026-01-05", source="generated")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    session = sessions.start_or_resume(task.id)
    user_text = "**raw user markdown stays literal**"
    sessions.append_user_message(session["id"], user_text)
    sessions.append_assistant_message(session["id"], MARKDOWN_EXAMPLE)
    before = [(row["role"], row["content"]) for row in sessions.messages(session["id"])]
    stored_assistant = before[1][1]
    assert "```json" in stored_assistant  # DB keeps raw Markdown, not rendered text

    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session(
        sessions.get(session["id"]),
        sessions.messages(session["id"]),
        task, None, model_configured=True,
    )

    after = [(row["role"], row["content"]) for row in sessions.messages(session["id"])]
    assert after == before
    assert after[0][1] == user_text
    assert after[1][1] == stored_assistant
    widgets = page.findChildren(AgentMessageWidget)
    assert widgets[0].raw_text == user_text
    assert widgets[1].raw_text == stored_assistant


def test_long_unbroken_text_wraps_without_horizontal_overflow(qtbot):
    from app.ui.agent_workspace_page import AgentWorkspacePage

    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.resize(360, 480)
    page.show()
    page._add_bubble(USER_SPEAKER, "u" * 400, USER_ROLE)
    page._add_bubble(ASSISTANT_SPEAKER, "a" * 400, ASSISTANT_ROLE)
    qtbot.waitExposed(page)
    viewport_width = page.conversation_scroll.viewport().width()
    qtbot.waitUntil(
        lambda: all(
            0 < widget.bubble.width() <= viewport_width
            for widget in page.findChildren(AgentMessageWidget)
        )
    )
    assert not page.conversation_scroll.horizontalScrollBar().isVisible()
    # Full text is still selectable/visible (wrapped), never clipped.
    user = page.findChildren(AgentMessageWidget)[0]
    assert user.plain_view.toPlainText() == "u" * 400


def test_markdown_view_restyles_on_theme_change(qtbot, qapp):
    from app.ui.design.theme_manager import ThemeManager

    manager = ThemeManager.instance()
    view = AgentMarkdownView()
    qtbot.addWidget(view)
    manager.set_theme("light")
    manager.apply(qapp)
    view.set_markdown("```json\n{}\n```", )
    light_html = view.document().toHtml()
    manager.set_theme("dark")
    manager.apply(qapp)
    dark_html = view.document().toHtml()
    assert light_html != dark_html
    assert colors.DARK_COLORS["surface_alt"].lower() in dark_html.lower()
    assert colors.LIGHT_COLORS["surface_alt"].lower() in light_html.lower()


def test_markdown_height_covers_fractional_layout_and_theme_resize(qtbot, qapp):
    import math
    from app.ui.agent_message_widget import BODY_HEIGHT_SLACK
    from app.ui.design.theme_manager import ThemeManager

    view = AgentMarkdownView()
    qtbot.addWidget(view)
    view.resize(377, 100)
    view.show()
    markdown = (
        "## SFT 基础\n\n中文段落：**监督微调**与 *训练*。\n\n"
        "- instruction\n- input\n\n1. 首先\n2. 然后\n\n"
        "`loss`\n\n```json\n{\"input\":\"hello\"}\n```\n\n"
        "| 字段 | 内容 |\n| --- | --- |\n| SFT | 示例 |\n\n\n\n最后一行。"
    )
    view.set_markdown(markdown)
    for theme in ("light", "dark"):
        ThemeManager.instance().set_theme(theme)
        ThemeManager.instance().apply(qapp)
        for width in (377, 501, 298):
            view.resize(width, view.height())
            qtbot.waitUntil(lambda: view.document().size().width() == view.viewport().width())
            qtbot.waitUntil(lambda: view.height() >= math.ceil(view.document().size().height())
                            + BODY_HEIGHT_SLACK)
            assert view.verticalScrollBar().maximum() == 0
            assert view.horizontalScrollBar().maximum() == 0


def test_long_pre_table_and_tokens_wrap_without_horizontal_loss(qtbot):
    view = AgentMarkdownView()
    qtbot.addWidget(view)
    view.resize(340, 100)
    view.show()
    md = (
        "中文" * 220 + "\n\n" + "x" * 400 + "\n\n"
        "```python\nprint('" + "a" * 500 + "')\n```\n\n"
        "```json\n{\"key\":\"" + "v" * 450 + "\"}\n```\n\n"
        "```\n<|im_start|>system" + "z" * 360 + "\n```\n\n"
        "| 列A | 列B |\n| --- | --- |\n| " + "t" * 300 + " | " + "u" * 300 + " |\n"
    )
    view.set_markdown(md)
    qtbot.waitUntil(lambda: view.document().size().height() > 200)
    assert view.document().idealWidth() <= view.viewport().width() + 2
    assert view.horizontalScrollBar().maximum() == 0
    block = view.document().begin()
    long_blocks = 0
    while block.isValid():
        if len(block.text()) >= 300:
            long_blocks += 1
            assert block.layout().lineCount() > 1, block.text()[:30]
        block = block.next()
    assert long_blocks >= 6


def test_message_identity_and_responsive_width(qtbot, repo):
    from app.ui.agent_workspace_page import AgentWorkspacePage
    task = repo.create(title="SFT", scheduled_date="2026-01-05", source="generated")
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.show()
    for width in (1200, 700, 380):
        page.resize(width, 640)
        page.load_session({"id": 1}, [
            {"id": 7, "role": "user", "content": "u" * 500},
            {"id": 8, "role": "assistant", "content": "正文" * 450},
            {"id": 9, "role": "assistant", "content": "", "tool_calls_json": '[{"id":"x"}]'},
            {"id": 10, "role": "tool", "content": "secret"},
        ], task, None, True)
        rows = page.findChildren(AgentMessageWidget)
        qtbot.waitUntil(lambda: rows[1].width() == rows[1].bubble.width()
                        == min(800, page.conversation_scroll.viewport().width()))
        viewport = page.conversation_scroll.viewport().width()
        assert [w.message_id for w in rows] == [7, 8]
        assert rows[0].bubble.width() <= min(USER_MAX_WIDTH, viewport * .80 + 2)
        assert rows[1].bubble.width() == min(ASSISTANT_MAX_WIDTH, viewport)
        assert not page.conversation_scroll.horizontalScrollBar().isVisible()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_hello_is_compact_fitted_and_keeps_accessible_identity(qtbot, qapp, theme):
    import math
    from PySide6.QtGui import QFontMetrics
    from app.ui.design.theme_manager import ThemeManager

    ThemeManager.instance().set_theme(theme)
    ThemeManager.instance().apply(qapp)
    for role in (USER_ROLE, ASSISTANT_ROLE):
        row = AgentMessageWidget(role, "hello", message_id=12)
        qtbot.addWidget(row)
        row.resize(800, row.sizeHint().height())
        row.show()
        view = row.plain_view or row.markdown_view
        qtbot.waitUntil(lambda: view.verticalScrollBar().maximum() == 0)
        assert row.raw_text == "hello" and row.message_id == 12
        assert row.accessibleName() == row.speaker
        assert view.viewport().height() >= math.ceil(view.document().size().height())
        line = QFontMetrics(view.document().defaultFont()).height()
        assert row.sizeHint().height() <= 3 * line + 8
        if role == USER_ROLE:
            assert row.speaker_label.isHidden()
            assert row.bubble.width() <= QFontMetrics(view.document().defaultFont()).horizontalAdvance("hello") + 36
            assert row.bubble.x() + row.bubble.width() == row.width()
        else:
            assert not row.speaker_label.isHidden()
            assert row.bubble.layout().contentsMargins().left() == 0
            assert row.bubble.width() == 800


@pytest.mark.parametrize("columns", [3, 5, 8])
def test_native_multicolumn_table_resizes_without_loss(qtbot, qapp, columns):
    from PySide6.QtGui import QTextCursor, QTextTable
    from app.ui.design.theme_manager import ThemeManager

    view = AgentMarkdownView()
    qtbot.addWidget(view)
    view.show()
    token = "D:/Projects/" + "original_path_" * 20
    markdown = ("| " + " | ".join(f"Col{i}" for i in range(columns)) + " |\n"
                + "| " + " | ".join(["---"] * columns) + " |\n"
                + "| " + " | ".join([token] * columns) + " |")
    view.set_markdown(markdown)
    for theme in ("light", "dark"):
        ThemeManager.instance().set_theme(theme)
        ThemeManager.instance().apply(qapp)
        for width in (800, 380, 280, 501):
            view.resize(width, view.height())
            qtbot.waitUntil(lambda: view.document().size().width() == view.viewport().width()
                           and view.horizontalScrollBar().maximum() == 0
                           and view.verticalScrollBar().maximum() == 0)
            table = next(f for f in view.document().rootFrame().childFrames()
                         if isinstance(f, QTextTable))
            assert table.columns() == columns
            for column in range(columns):
                block = table.cellAt(1, column).firstCursorPosition().block()
                assert block.text() == token
                assert block.layout().lineCount() > 1
                cursor = QTextCursor(block)
                cursor.setPosition(block.position())
                cursor.setPosition(block.position() + len(token), QTextCursor.MoveMode.KeepAnchor)
                assert cursor.selectedText() == token
                view.setTextCursor(cursor)
                view.copy()
                assert qapp.clipboard().text() == token
            assert view.markdown == markdown


def test_table_after_code_has_no_generated_header_gap_or_code_surface(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QTextCursor, QTextFrameFormat, QTextTable

    markdown = '```python\na = "original/path"\n```\n\n| Stage | Check |\n| --- | --- |\n| SFT | loss |'
    view = AgentMarkdownView()
    qtbot.addWidget(view)
    view.resize(380, 100)
    view.show()
    view.set_markdown(markdown)
    qtbot.waitUntil(lambda: view.horizontalScrollBar().maximum() == 0)
    table = next(f for f in view.document().rootFrame().childFrames() if isinstance(f, QTextTable))
    for column, header in enumerate(("Stage", "Check")):
        block = table.cellAt(0, column).firstCursorPosition().block()
        assert block.text() == header
        assert block.blockFormat().background().style() == Qt.BrushStyle.NoBrush
        assert block.blockFormat().leftMargin() == 0
        assert QTextCursor(block).charFormat().fontWeight() == QFont.Weight.Bold
        cell_format = table.cellAt(0, column).format().toTableCellFormat()
        assert cell_format.topBorder() == .5
        assert cell_format.topBorderStyle() == QTextFrameFormat.BorderStyle.BorderStyle_Solid
    assert view.markdown == markdown
    assert 'a = "original/path"' in view.toPlainText()
