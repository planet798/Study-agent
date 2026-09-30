"""Practice detail scroll restoration: one generation, user intent, safe teardown."""
from __future__ import annotations

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from app.ui.practice_page import PracticeProjectDetailDialog


def _detail(practice_env, qtbot, *, register=True):
    env = practice_env
    project = env.service.create_project("Scrollable Practice", "other")
    milestones = [env.service.add_milestone(project["id"], f"milestone {i}")
                  for i in range(35)]
    dialog = PracticeProjectDetailDialog(project["id"], env.service,
                                         env.route_repo, env.skill_repo,
                                         env.plan_repo)
    if register:
        qtbot.addWidget(dialog)
    dialog.resize(680, 400)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.scroll.verticalScrollBar().maximum() > 0)
    return dialog, milestones


def _settle(qtbot, dialog, expected):
    bar = dialog.scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: dialog._scroll_restore_handler is None
                    and bar.value() == min(expected, bar.maximum()))


def test_midpage_refresh_preserves_position(practice_env, qtbot):
    dialog, _ = _detail(practice_env, qtbot)
    bar = dialog._scroll_bar
    target = bar.maximum() // 2
    bar.setValue(target)
    dialog.refresh()
    _settle(qtbot, dialog, target)
    assert bar.value() == target


def test_shrinking_content_clamps_to_new_maximum(practice_env, qtbot):
    dialog, milestones = _detail(practice_env, qtbot)
    bar = dialog._scroll_bar
    bar.setValue(bar.maximum())
    before = bar.value()
    for milestone in milestones[5:]:
        practice_env.service.delete_milestone(milestone["id"])
    dialog.refresh()
    qtbot.waitUntil(lambda: bar.maximum() < before and dialog._scroll_restore_handler is None)
    assert bar.value() == min(before, bar.maximum())


def test_consecutive_restore_and_late_old_callback_cannot_override(practice_env, qtbot):
    dialog, _ = _detail(practice_env, qtbot)
    bar = dialog._scroll_bar
    bar.setRange(0, 0)  # layout pending
    dialog._restore_scroll(500)
    old = dialog._scroll_restore_handler
    dialog._restore_scroll(180)
    assert old is not dialog._scroll_restore_handler
    old(0, 600)  # queued range event from generation A
    bar.setRange(0, 600)
    _settle(qtbot, dialog, 180)
    old(0, 600)
    assert bar.value() == 180


def test_slider_cancels_pending_restore(practice_env, qtbot):
    dialog, _ = _detail(practice_env, qtbot)
    bar = dialog._scroll_bar
    bar.setRange(0, 0)
    dialog._restore_scroll(430)
    old = dialog._scroll_restore_handler
    bar.sliderPressed.emit()
    assert dialog._scroll_restore_handler is None
    bar.setRange(0, 600)
    bar.setValue(120)
    old(0, 600)
    assert bar.value() == 120


def test_wheel_cancels_pending_restore(practice_env, qtbot):
    dialog, _ = _detail(practice_env, qtbot)
    bar = dialog._scroll_bar
    bar.setRange(0, 0)
    dialog._restore_scroll(430)
    old = dialog._scroll_restore_handler
    viewport = dialog.scroll.viewport()
    pos = QPointF(10, 10)
    wheel = QWheelEvent(pos, QPointF(viewport.mapToGlobal(QPoint(10, 10))),
                        QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
    QApplication.sendEvent(viewport, wheel)
    assert dialog._scroll_restore_handler is None
    bar.setRange(0, 600)
    bar.setValue(130)
    old(0, 600)
    assert bar.value() == 130


def test_viewport_press_cancels_pending_restore(practice_env, qtbot):
    dialog, _ = _detail(practice_env, qtbot)
    bar = dialog._scroll_bar
    bar.setRange(0, 0)
    dialog._restore_scroll(430)
    old = dialog._scroll_restore_handler
    qtbot.mousePress(dialog.scroll.viewport(), Qt.MouseButton.LeftButton)
    qtbot.mouseRelease(dialog.scroll.viewport(), Qt.MouseButton.LeftButton)
    assert dialog._scroll_restore_handler is None
    bar.setRange(0, 600)
    bar.setValue(140)
    old(0, 600)
    assert bar.value() == 140


def test_close_and_accept_clear_pending_restore(practice_env, qtbot):
    for close_method in ("close", "accept", "reject"):
        dialog, _ = _detail(practice_env, qtbot)
        bar = dialog._scroll_bar
        bar.setRange(0, 0)
        dialog._restore_scroll(500)
        old = dialog._scroll_restore_handler
        getattr(dialog, close_method)()
        assert dialog._scroll_restore_handler is None
        bar.setRange(0, 600)
        bar.setValue(90)
        old(0, 600)
        assert bar.value() == 90


def test_deferred_delete_with_pending_restore_has_no_stale_handler(practice_env, qtbot):
    dialog, _ = _detail(practice_env, qtbot, register=False)
    dialog._scroll_bar.setRange(0, 0)
    dialog._restore_scroll(500)
    old = dialog._scroll_restore_handler
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
    assert dialog._scroll_restore_handler is None
    assert not shiboken6.isValid(dialog)
    old(0, 600)  # epoch check does not touch the deleted scrollbar


def test_repeated_refresh_with_range_churn_preserves_latest_position(practice_env, qtbot):
    dialog, _ = _detail(practice_env, qtbot)
    bar = dialog._scroll_bar
    for requested in (350, 180, 430, 90):
        bar.setValue(requested)
        captured = bar.value()
        dialog.refresh()
        bar.setRange(0, 0)
        dialog._restore_scroll(captured)
        bar.setRange(0, 700)
        _settle(qtbot, dialog, captured)
    assert bar.value() == 90


def test_top_refresh_has_no_pending_restore(practice_env, qtbot):
    dialog, _ = _detail(practice_env, qtbot)
    dialog._scroll_bar.setValue(0)
    dialog.refresh()
    assert dialog._scroll_restore_handler is None
    assert dialog._scroll_bar.value() == 0
