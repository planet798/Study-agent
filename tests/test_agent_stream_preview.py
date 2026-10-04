from app.ui.agent_workspace_page import AgentWorkspacePage


def test_stream_is_transient_plain_text_and_session_scoped(qtbot, repo):
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    task = repo.create(title="stream", scheduled_date="2026-01-01")
    page.load_session({"id": 1}, [], task, None, True)
    page.set_busy(True)
    page.handle_stream_event(2, {"event": "text_delta", "text": "wrong"})
    assert page._stream_preview is None
    page.handle_stream_event(1, {"event": "thinking_delta", "text": "private"})
    assert page._stream_preview is None
    page.handle_stream_event(1, {"event": "text_delta", "text": "<b>text</b>"})
    page._flush_stream()
    assert page._stream_preview.body.text() == "<b>text</b>"
    page.handle_stream_event(1, {"event": "discard_text"})
    assert page._stream_text == "" and page._stream_preview.isHidden()
    page.handle_stream_event(1, {"event": "text_delta", "text": "final draft"})
    page.load_session({"id": 1}, [{"id": 1, "role": "assistant", "content": "final"}], task, None, True)
    assert page._stream_preview is None and page._stream_text == ""
