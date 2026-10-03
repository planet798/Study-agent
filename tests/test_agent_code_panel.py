"""Native panel safety, original-source clipboard, independent geometry and scroll."""
import math

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QUrl
from PySide6.QtGui import QFontMetrics, QTextCursor, QTextDocument, QTextFormat, QTextTable
from PySide6.QtWidgets import QLabel, QScrollArea

from app.ui.agent_code_panel import AgentAssistantContent, AgentCodePanel
from app.ui.agent_content_blocks import segment_agent_content
from app.ui.agent_message_widget import AgentMarkdownView, AgentMessageWidget, _is_code_block
from app.ui.design.theme_manager import ThemeManager


@pytest.fixture(autouse=True)
def restore_theme(qapp):
    yield
    ThemeManager.instance().set_theme("light")


def make_panel(qtbot, source, width=500, *, managed=True):
    panel = AgentCodePanel(segment_agent_content(source)[0])
    if managed:
        qtbot.addWidget(panel)
    panel.resize(width, panel.sizeHint().height())
    panel.show()
    qtbot.waitUntil(lambda: panel.clip.width() > 10)
    panel.clip.recalculate()
    return panel


@pytest.mark.parametrize("language", ["python", "text", "unknown", "", "https://example.com", "MD", "markdown"])
def test_language_policy_and_literal_heading(qtbot, language):
    panel = make_panel(qtbot, f"```{language}\n# not auto-guessed\n```\n")
    assert (panel.preview_view is not None) == (language.lower() in ("md", "markdown"))
    assert panel.source_view.toPlainText() == "# not auto-guessed\n"
    if panel.preview_view is not None:
        assert panel.clip.view is panel.preview_view
        assert panel.preview_button.isChecked()
        assert "# " not in panel.preview_view.toPlainText()
    else:
        assert panel.clip.view is panel.source_view
    label = panel.findChild(QLabel)
    assert label.text() == (language or "纯文本")
    assert label.textFormat() == Qt.TextFormat.PlainText


@pytest.mark.parametrize("language", ["markdown", "python", ""])
def test_exact_copy_all_tabs_collapsed_expanded(qtbot, qapp, language):
    payload = "  leading\r\n\r\n```inner\r\n" + "\r\n".join(f"line {i}  " for i in range(60)) + "\r\n"
    panel = make_panel(qtbot, f"````{language}\r\n{payload}````\r\n")
    assert panel.block.payload == payload
    for preview in ([True, False] if panel.preview_view else [False]):
        panel.set_preview(preview)
        for expanded in (False, True):
            if panel.clip.expanded != expanded:
                panel.toggle_expanded()
            qtbot.waitUntil(lambda: panel.clip.overflow)
            qtbot.mouseClick(panel.copy_button, Qt.MouseButton.LeftButton)
            assert qapp.clipboard().text() == payload
            assert panel.copy_button.text() == "已复制"
    assert panel.source_view.isReadOnly()
    assert panel.source_view.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    assert panel.findChildren(QScrollArea) == []
    for view in (panel.source_view, panel.preview_view):
        if view is not None:
            assert view.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            assert view.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_copy_feedback_timer_restarts_and_cleanup(qtbot, qapp):
    panel = make_panel(qtbot, "```\n\n```\n", managed=False)
    panel.copy_source()
    assert qapp.clipboard().text() == "\n"
    panel._copy_timer.setInterval(30)
    panel.copy_source()
    assert panel._copy_timer.interval() == 1200
    panel._copy_timer.start(10)
    qtbot.waitUntil(lambda: panel.copy_button.text() == "复制")
    panel.copy_source()
    panel.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qtbot.wait(20)  # child-bound timers cannot touch the deleted button


def test_empty_panel_copy_and_no_fake_expand(qtbot, qapp):
    panel = make_panel(qtbot, "```\n```\n")
    panel.copy_source()
    assert qapp.clipboard().text() == ""
    assert not panel.clip.overflow
    assert panel.expand_button.isHidden()
    assert panel.source_view.viewport().height() >= math.ceil(panel.source_view.document().size().height())


def test_message_order_identity_and_markdown_contract(qtbot):
    raw = "Intro\n\n```python\na\n```\n\nMiddle\n\n~~~md\n# H\n~~~\n\nEnd"
    row = AgentMessageWidget("assistant", raw, message_id=17)
    qtbot.addWidget(row)
    assert isinstance(row.markdown_view, AgentAssistantContent)
    assert row.markdown_view.markdown == row.raw_text == row.text == raw
    assert row.message_id == 17 and row.role == "assistant"
    assert row.accessibleName() == "学习助手"
    assert [type(v) for v in row.markdown_view.views] == [AgentMarkdownView, AgentCodePanel, AgentMarkdownView, AgentCodePanel, AgentMarkdownView]
    assert row.markdown_view.panels[1].clip.view is row.markdown_view.panels[1].preview_view
    assert "Intro" in row.markdown_view.toPlainText()
    assert "End" in row.markdown_view.toPlainText()


@pytest.mark.parametrize("raw", [
    "See [multi\nline].\n\n```py\nx\n```\n\n[multi\nline]: https://example.com\n",
    "See [a\\]b].\n\n```py\nx\n```\n\n[a\\]b]: https://example.com\n",
])
def test_complex_reference_fallback_matches_native_text_and_anchors(qtbot, raw):
    native = AgentMarkdownView()
    native.set_markdown(raw)
    qtbot.addWidget(native)
    row = AgentMessageWidget("assistant", raw)
    qtbot.addWidget(row)
    assert type(row.markdown_view) is AgentMarkdownView
    assert row.raw_text == row.markdown_view.markdown == raw
    assert not row.findChildren(AgentCodePanel)
    assert row.markdown_view.toPlainText() == native.toPlainText()

    def anchors(view):
        result = []
        block = view.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().isAnchor():
                    result.append((fragment.text(), fragment.charFormat().anchorHref()))
                iterator += 1
            block = block.next()
        return result

    assert anchors(native)
    assert anchors(row.markdown_view) == anchors(native)


def test_container_and_reference_fallback_preserves_whole_native_document(qtbot):
    for raw in ("1. first\n\n```py\nx\n```\n\n2. second",
                "[link][ref]\n\n```py\nx\n```\n\n[ref]: https://example.com",
                "> ```md\n> # nested\n> ```"):
        row = AgentMessageWidget("assistant", raw)
        qtbot.addWidget(row)
        assert type(row.markdown_view) is AgentMarkdownView
        assert row.markdown_view.markdown == raw
        assert not row.findChildren(AgentCodePanel)
        assert row.raw_text == raw


def test_preview_resources_html_and_links_are_inert(qtbot):
    payload = ("<script>alert(1)</script>\n\n<h1>evil</h1>\n\n"
               "![remote](https://example.com/x.png)\n\n![local](file:///secret)\n\n![qrc](qrc:/secret)\n\n"
               "[remote](https://example.com) [local](file:///secret) [qrc](qrc:/secret)\n")
    panel = make_panel(qtbot, f"````md\n{payload}````\n")
    view = panel.preview_view
    assert view.markdown == payload
    html = view.document().toHtml()
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<h1>" not in html
    assert view.openLinks() is False and view.openExternalLinks() is False
    for url in ("https://example.com/x.png", "file:///secret", "qrc:/secret"):
        assert view.loadResource(QTextDocument.ResourceType.ImageResource, QUrl(url)) is None
        resource = view.document().resource(QTextDocument.ResourceType.ImageResource, QUrl(url))
        assert resource is None or not getattr(resource, "isValid", lambda: True)()
    panel.set_preview(False)
    assert "<script>" in panel.source_view.toPlainText()
    assert panel.findChildren(AgentCodePanel) == []  # preview is never recursively segmented


def test_code_metadata_not_all_mono_heuristic_and_continuous_nested_background(qtbot):
    panel = make_panel(qtbot, "````md\n`only inline`\n\n```py\na\n\nb\n```\n\n## Heading\n\n- list\n\n> quote\n````")
    view = panel.preview_view
    document = view.document()
    first = document.begin()
    assert first.text() == "only inline"
    assert not _is_code_block(first)
    assert first.blockFormat().background().style() == Qt.BrushStyle.NoBrush
    frames = [f for f in document.rootFrame().childFrames() if not isinstance(f, QTextTable)]
    assert len(frames) == 1
    frame = frames[0]
    assert frame.format().background().style() != Qt.BrushStyle.NoBrush
    block = frame.firstCursorPosition().block()
    seen = []
    while block.isValid() and block.position() <= frame.lastPosition():
        seen.append(block.text())
        assert block.blockFormat().topMargin() == block.blockFormat().bottomMargin() == 0
        assert block.blockFormat().background().style() == Qt.BrushStyle.NoBrush
        block = block.next()
    assert seen == ["a", "", "b"]
    assert "Heading" in view.toPlainText() and "list" in view.toPlainText() and "quote" in view.toPlainText()
    assert any(b.textList() for b in iter_blocks(document))


def iter_blocks(document):
    block = document.begin()
    while block.isValid():
        yield block
        block = block.next()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_width_theme_font_and_long_table_code_geometry(qtbot, qapp, theme):
    ThemeManager.instance().set_theme(theme)
    ThemeManager.instance().apply(qapp)
    token = "original_path_" * 30
    payload = "## H\n\n```py\n" + (token + "\n") * 35 + "```\n\n| A | B | C |\n| --- | --- | --- |\n" + (f"| {token} | {token} | {token} |\n") * 12 + "\nFinal line\n"
    panel = make_panel(qtbot, f"````md\n{payload}````\n")
    for width in (800, 380, 280, 501):
        panel.resize(width, panel.height())
        for preview in (True, False, True):
            panel.set_preview(preview)
            view = panel.clip.view
            qtbot.waitUntil(lambda: view.width() == panel.clip.width()
                           and view.horizontalScrollBar().maximum() == 0
                           and view.verticalScrollBar().maximum() == 0)
            assert panel.clip.overflow
            panel.clip.expanded = False
            panel.clip.recalculate()
            assert panel.clip.height() <= 14 * QFontMetrics(view.document().defaultFont()).lineSpacing() + 2
            assert panel.expand_button.text() == "展开全文"
            panel.toggle_expanded()
            qtbot.waitUntil(lambda: panel.clip.height() == view.height())
            assert view.viewport().height() >= math.ceil(view.document().size().height())
            assert panel.expand_button.text() == "收起"
            assert panel.block.payload == payload
            if preview:
                assert view.toPlainText().endswith("Final line")
                assert any(isinstance(f, QTextTable) for f in view.document().rootFrame().childFrames())
        font = panel.font()
        font.setPointSize(14)
        panel.setFont(font)
        qtbot.waitUntil(lambda: panel.clip.height() == panel.clip.view.height())
        assert panel.source_view.document().defaultFont().fixedPitch()


def test_independent_children_switch_heights_and_user_signal_only(qtbot):
    panel = make_panel(qtbot, "```md\n" + "\n" * 80 + "# Short\n```\n")
    events = []
    panel.content_interaction.connect(lambda: events.append(panel.clip.view))
    assert not panel.clip.overflow
    preview_height = panel.clip.height()
    panel.set_preview(False)
    qtbot.waitUntil(lambda: panel.clip.overflow)
    assert panel.clip.height() > preview_height
    panel.toggle_expanded()
    panel.set_preview(True)
    qtbot.waitUntil(lambda: panel.clip.height() == panel.preview_view.height())
    assert panel.clip.height() == preview_height
    assert not panel.clip.overflow
    assert len(events) == 3
    panel.resize(300, panel.height())
    ThemeManager.instance().set_theme("dark")
    qtbot.wait(20)
    assert len(events) == 3  # theme/resize must not cancel initial scroll positioning


def test_expansion_cancels_old_scroll_without_bottom_jump(qtbot):
    from app.ui.agent_workspace_page import AgentWorkspacePage

    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.resize(900, 600)
    page.show()
    row = page._add_bubble("学习助手", "```md\n" + "line\n\n" * 100 + "```\n", "assistant", 29)
    for _ in range(15):
        page._add_bubble("学习助手", "following\n\n" * 8, "assistant")
    panel = row.markdown_view.panels[0]
    bar = page.conversation_scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 500)
    assert row.parent() is page.conversation_body
    page._schedule_scroll(to_bottom=True)
    qtbot.waitUntil(lambda: bar.value() == bar.maximum())
    assert page._pending_scroll_handler is not None
    page._programmatic_scroll = True
    bar.setValue(60)
    page._programmatic_scroll = False
    position = bar.value()
    panel.toggle_expanded()
    qtbot.wait(30)
    assert page._pending_scroll_handler is None
    assert bar.value() == position and bar.value() < bar.maximum()
    panel.set_preview(False)
    qtbot.wait(30)
    assert bar.value() == position
    panel.toggle_expanded()
    qtbot.wait(30)
    assert bar.value() == position
    assert row.message_id == 29
    page.clear_session()
    qtbot.wait(20)
    assert page.findChildren(AgentCodePanel) == []


def test_preview_container_code_keeps_semantics_without_paragraph_gaps(qtbot):
    payload = "> ```py\n> a\n>\n> b\n> ```\n\n- item\n\n  ```py\n  c\n  d\n  ```\n"
    panel = make_panel(qtbot, f"````md\n{payload}````\n")
    document = panel.preview_view.document()
    assert "item" in panel.preview_view.toPlainText()
    quoted = [b for b in iter_blocks(document) if b.blockFormat().property(QTextFormat.Property.BlockQuoteLevel)]
    assert quoted
    assert all(b.blockFormat().leftMargin() >= 40 for b in quoted)
    code = [b for b in iter_blocks(document) if _is_code_block(b)]
    assert [b.text() for b in code] == ["a", "", "b", "c", "d"]
    assert all(b.blockFormat().topMargin() == b.blockFormat().bottomMargin() == 0 for b in code)


def test_long_info_header_does_not_widen_conversation(qtbot):
    row = AgentMessageWidget("assistant", "```" + "long-language-" * 100 + "\nx\n```\n")
    qtbot.addWidget(row)
    row.resize(320, row.sizeHint().height())
    row.show()
    panel = row.markdown_view.panels[0]
    qtbot.waitUntil(lambda: panel.width() <= 320)
    assert row.minimumSizeHint().width() <= 320
    assert panel.copy_button.isVisible()
    assert panel.block.payload == "x\n"
