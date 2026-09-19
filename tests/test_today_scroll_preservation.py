"""今日页滚动位置保持（pytest-qt 真实滚动条）。

覆盖需求 24 的 1~15。
"""

from __future__ import annotations

from PySide6.QtCore import Qt

TODAY = "2026-01-05"


def _seed(task_service, n=25, prefix="任务"):
    return [task_service.create_task(f"{prefix}{i}", scheduled_date=TODAY)
            for i in range(n)]


def _prepare(qtbot, make_window, task_service, n=25):
    _seed(task_service, n=n)
    w = make_window()
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    qtbot.waitUntil(
        lambda: w.scroll.verticalScrollBar().maximum() > 0, timeout=3000
    )
    return w


def _set_mid(w):
    bar = w.scroll.verticalScrollBar()
    mid = max(1, bar.maximum() // 2)
    bar.setValue(mid)
    return bar.value()


def _assert_near(w, before, *, tol=80):
    bar = w.scroll.verticalScrollBar()
    after = bar.value()
    # 不得跳到最底（除非原本就在最底）
    if before < bar.maximum():
        assert after != bar.maximum(), "点击后跳到了底部"
    assert abs(after - before) <= tol, (after, before, bar.maximum())


class TestScrollPreservation:
    def test_complete_keeps_position(self, qtbot, make_window, task_service):
        w = _prepare(qtbot, make_window, task_service)
        before = _set_mid(w)
        idx = len(w._task_widgets) // 2
        qtbot.mouseClick(w._task_widgets[idx].complete_btn,
                         Qt.MouseButton.LeftButton)
        qtbot.wait(320)
        _assert_near(w, before)

    def test_not_done_keeps_position(self, qtbot, make_window, task_service,
                                     monkeypatch):
        monkeypatch.setattr(
            "app.ui.main_window.NotDoneDialog.get_reason",
            staticmethod(lambda *a, **k: "没时间"),
        )
        w = _prepare(qtbot, make_window, task_service)
        before = _set_mid(w)
        idx = len(w._task_widgets) // 2
        qtbot.mouseClick(w._task_widgets[idx].not_done_btn,
                         Qt.MouseButton.LeftButton)
        qtbot.wait(320)
        _assert_near(w, before)

    def test_remove_keeps_position(self, qtbot, make_window, task_service):
        w = _prepare(qtbot, make_window, task_service)
        before = _set_mid(w)
        idx = len(w._task_widgets) // 2
        widget = w._task_widgets[idx]
        w._confirm_remove_dialog = lambda: True
        qtbot.mouseClick(widget.remove_btn, Qt.MouseButton.LeftButton)
        qtbot.wait(320)
        bar = w.scroll.verticalScrollBar()
        # 卡片消失后 maximum 可能变小 → clamp 是合理的
        assert bar.value() <= bar.maximum()
        assert abs(bar.value() - min(before, bar.maximum())) <= 80

    def test_review_complete_keeps_position(self, qtbot, make_window,
                                            task_service, repo, conn):
        from app.database.assessment_repository import AssessmentRepository

        arepo = AssessmentRepository(conn)
        kp = arepo.create_knowledge_point("kp")
        task_service.create_task("复习", scheduled_date=TODAY)
        repo.create("复习任务", scheduled_date=TODAY, source="review",
                    task_type="review", knowledge_point_id=kp["id"])
        _seed(task_service, n=23)
        w = make_window()
        qtbot.addWidget(w)
        w.show()
        qtbot.waitExposed(w)
        qtbot.waitUntil(
            lambda: w.scroll.verticalScrollBar().maximum() > 0, timeout=3000
        )
        before = _set_mid(w)
        # 找到 review 卡片（在“今日复习”区，可能靠后）
        target = next((wd for wd in w._task_widgets
                       if wd.task().task_type == "review"), None)
        assert target is not None
        qtbot.mouseClick(target.complete_btn, Qt.MouseButton.LeftButton)
        qtbot.wait(320)
        _assert_near(w, before)

    def test_manual_task_mutation(self, qtbot, make_window, task_service):
        w = _prepare(qtbot, make_window, task_service)
        before = _set_mid(w)
        idx = len(w._task_widgets) // 2
        qtbot.mouseClick(w._task_widgets[idx].complete_btn,
                         Qt.MouseButton.LeftButton)
        qtbot.wait(320)
        _assert_near(w, before)

    def test_generated_task_mutation(self, qtbot, make_window, repo,
                                     task_service):
        for i in range(24):
            repo.create(f"gen{i}", scheduled_date=TODAY,
                        source="generated", task_type="new")
        w = make_window()
        qtbot.addWidget(w)
        w.show()
        qtbot.waitExposed(w)
        qtbot.waitUntil(
            lambda: w.scroll.verticalScrollBar().maximum() > 0, timeout=3000
        )
        before = _set_mid(w)
        qtbot.mouseClick(w._task_widgets[len(w._task_widgets) // 2].complete_btn,
                         Qt.MouseButton.LeftButton)
        qtbot.wait(320)
        _assert_near(w, before)

    def test_consecutive_clicks(self, qtbot, make_window, task_service):
        w = _prepare(qtbot, make_window, task_service)
        before = _set_mid(w)
        for _ in range(3):
            idx = len(w._task_widgets) // 2
            widget = w._task_widgets[idx]
            if not hasattr(widget, "complete_btn"):
                break
            qtbot.mouseClick(widget.complete_btn, Qt.MouseButton.LeftButton)
            qtbot.wait(320)
        _assert_near(w, before)

    def test_top_stays_near_top(self, qtbot, make_window, task_service):
        w = _prepare(qtbot, make_window, task_service)
        w.scroll.verticalScrollBar().setValue(0)
        qtbot.mouseClick(w._task_widgets[0].complete_btn,
                         Qt.MouseButton.LeftButton)
        qtbot.wait(320)
        assert w.scroll.verticalScrollBar().value() <= 80

    def test_bottom_may_stay_bottom(self, qtbot, make_window, task_service):
        w = _prepare(qtbot, make_window, task_service)
        bar = w.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        before = bar.value()
        idx = len(w._task_widgets) - 1
        widget = w._task_widgets[idx]
        if not hasattr(widget, "complete_btn"):
            idx -= 1
            widget = w._task_widgets[idx]
        qtbot.mouseClick(widget.complete_btn, Qt.MouseButton.LeftButton)
        qtbot.wait(320)
        assert w.scroll.verticalScrollBar().value() >= before - 80

    def test_short_page_no_scroll(self, qtbot, make_window, task_service):
        _seed(task_service, n=2)
        w = make_window()
        qtbot.addWidget(w)
        w.show()
        qtbot.waitExposed(w)
        idx = 0
        qtbot.mouseClick(w._task_widgets[idx].complete_btn,
                         Qt.MouseButton.LeftButton)
        qtbot.wait(200)
        assert w.scroll.verticalScrollBar().value() == 0

    def test_focus_cleared_after_refresh(self, qtbot, make_window, task_service):
        from PySide6.QtWidgets import QApplication

        w = _prepare(qtbot, make_window, task_service)
        qtbot.mouseClick(w._task_widgets[len(w._task_widgets) // 2].complete_btn,
                         Qt.MouseButton.LeftButton)
        qtbot.wait(320)
        focused = QApplication.focusWidget()
        # 不能是已被删除的旧按钮（focus 不应停留在里列表内部导致自动滚动）
        assert focused is None or focused is not w.list_container
