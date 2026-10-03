"""UX-3: message-aware scrolling and latest-reply affordance."""

from PySide6.QtCore import QPoint

from app.ui.agent_message_widget import AgentMessageWidget
from app.ui.agent_workspace_page import AgentWorkspacePage


def _row(mid, role, text):
    return {"id": mid, "role": role, "content": text, "tool_calls_json": ""}


def _page(qtbot, repo):
    task = repo.create(title="SFT 理论学习", scheduled_date="2026-01-05",
                       source="generated")
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.resize(780, 630)
    page.show()
    return page, task


def _history():
    return [_row(i, "user" if i % 2 else "assistant", "历史段落 " * 25)
            for i in range(1, 31)]


def _answer():
    return "## SFT 是什么\n\n" + ("监督微调是有监督训练。" * 30 + "\n\n") * 18


def test_open_history_starts_at_bottom(qtbot, repo):
    page, task = _page(qtbot, repo)
    page.load_session({"id": 1}, _history(), task, None, True)
    bar = page.conversation_scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0 and bar.value() == bar.maximum())
    assert page.latest_button.isHidden()


def test_new_long_assistant_starts_at_its_top_not_its_end(qtbot, repo):
    page, task = _page(qtbot, repo)
    history = _history()
    page.load_session({"id": 1}, history, task, None, True)
    bar = page.conversation_scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0 and bar.value() == bar.maximum())
    page.load_session({"id": 1}, history + [_row(31, "assistant", _answer())],
                      task, None, True)
    widget = next(w for w in page.findChildren(AgentMessageWidget) if w.message_id == 31)
    def target():
        return min(max(0, widget.mapTo(page.conversation_body, QPoint()).y() - 16), bar.maximum())
    qtbot.waitUntil(lambda: bar.maximum() > target() + 100 and abs(bar.value()-target()) <= 2)
    assert bar.value() < bar.maximum() - 100
    assert widget.markdown_view.toPlainText().startswith("SFT 是什么")
    assert page.latest_button.text() == "↓ 最新"


def test_approval_refresh_preserves_scroll(qtbot, repo):
    page, task = _page(qtbot, repo)
    history = _history()
    page.load_session({"id": 1}, history, task, None, True)
    bar = page.conversation_scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0)
    bar.setValue(bar.maximum() // 3)
    saved = bar.value()
    page.load_session({"id": 1}, history, task, None, True,
                      approvals=[{"id": 2, "status": "pending",
                                  "tool_name": "request_complete_current_task"}])
    qtbot.waitUntil(lambda: abs(bar.value()-saved) <= 2)
    assert abs(bar.value()-saved) <= 2


def test_busy_history_reading_does_not_steal_scroll_and_latest_click(qtbot, repo):
    page, task = _page(qtbot, repo)
    history = _history()
    page.load_session({"id": 1}, history, task, None, True)
    bar = page.conversation_scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0 and bar.value() == bar.maximum())
    page.set_busy(True)
    bar.setValue(bar.maximum() // 4)  # manual reading during the model turn
    saved = bar.value()
    page.load_session({"id": 1}, history + [_row(31, "assistant", _answer())],
                      task, None, True, turn_busy=False)
    qtbot.waitUntil(lambda: abs(bar.value()-saved) <= 2 and not page.latest_button.isHidden())
    assert page.latest_button.text() == "新回复 ↓"
    page.latest_button.click()
    qtbot.waitUntil(lambda: bar.value() == bar.maximum())
    assert page.latest_button.isHidden()


def test_session_switch_cancels_old_assistant_target(qtbot, repo):
    page, task = _page(qtbot, repo)
    history = _history()
    page.load_session({"id": 1}, history, task, None, True)
    page.load_session({"id": 1}, history + [_row(31, "assistant", _answer())],
                      task, None, True)
    page.load_session({"id": 2}, [_row(50, "user", "B 的消息 " * 600)],
                      task, None, True)
    bar = page.conversation_scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0 and bar.value() == bar.maximum())
    qtbot.wait(220)  # old A timers must remain inert
    assert bar.value() == bar.maximum()
    assert page.current_session_id == 2
    assert not any(w.message_id == 31 for w in page.findChildren(AgentMessageWidget))


def test_conversation_reload_leaves_no_orphan_top_level_bubbles(qtbot, repo):
    from PySide6.QtWidgets import QApplication

    page, task = _page(qtbot, repo)
    history = _history()
    for _ in range(12):
        page.load_session({"id": 1}, history, task, None, True)
    QApplication.processEvents()
    assert not [widget for widget in QApplication.topLevelWidgets()
                if isinstance(widget, AgentMessageWidget)]
    page.close()


def test_scroll_restore_tracks_late_layout_range_and_clears_on_close(qtbot, repo):
    page, task = _page(qtbot, repo)
    page.load_session({"id": 1}, _history(), task, None, True)
    bar = page.conversation_scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 300)
    page._restore_scroll(180)
    qtbot.waitUntil(lambda: bar.value() == 180)
    # A second layout change must not lose the pending restore target.
    bar.setValue(0)
    bar.setRange(0, bar.maximum() + 20)
    qtbot.waitUntil(lambda: bar.value() == 180)
    page.close()
    assert page._pending_scroll_handler is None
    bar.setRange(0, bar.maximum() + 20)  # no stale callback after close
    assert bar.value() == 180


def test_new_assistant_after_user_and_hidden_tool_targets_assistant(qtbot, repo):
    page, task = _page(qtbot, repo)
    history = _history()
    page.load_session({"id": 1}, history, task, None, True)
    bar = page.conversation_scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0 and bar.value() == bar.maximum())
    extended = history + [
        _row(31, "user", "继续讲"),
        {"id": 32, "role": "assistant", "content": "", "tool_calls_json": '[{"id":"secret"}]'},
        {"id": 33, "role": "tool", "content": "internal result"},
        _row(34, "assistant", _answer()),
    ]
    page.load_session({"id": 1}, extended, task, None, True)
    rows = page.findChildren(AgentMessageWidget)
    assert [w.message_id for w in rows[-2:]] == [31, 34]
    widget = rows[-1]
    qtbot.waitUntil(lambda: bar.maximum() >
                    widget.mapTo(page.conversation_body, QPoint()).y() + 100
                    and abs(bar.value() - min(
                        max(0, widget.mapTo(page.conversation_body, QPoint()).y() - 16),
                        bar.maximum())) <= 2)
    assert bar.value() < bar.maximum() - 100


def test_busy_without_manual_scroll_still_targets_new_assistant(qtbot, repo):
    page, task = _page(qtbot, repo)
    history = _history()
    page.load_session({"id": 1}, history, task, None, True)
    bar = page.conversation_scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0 and bar.value() == bar.maximum())
    page.set_busy(True)
    page.load_session({"id": 1}, history + [_row(31, "assistant", _answer())],
                      task, None, True, turn_busy=False)
    widget = next(w for w in page.findChildren(AgentMessageWidget) if w.message_id == 31)
    qtbot.waitUntil(lambda: bar.maximum() > widget.mapTo(page.conversation_body, QPoint()).y() + 100
                    and abs(bar.value() - min(max(0, widget.mapTo(
                        page.conversation_body, QPoint()).y() - 16), bar.maximum())) <= 2)
    assert page.latest_button.text() == "↓ 最新"
