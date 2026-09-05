"""GUI 层测试：主窗口 / 任务卡片 / 对话框 / 统计 / 日期处理 / AI 复核。

注意：所有日期都通过 today_provider 注入固定值（2026-01-05），
不依赖真实系统日期；截图/交互通过 qtbot（offscreen 平台）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QSystemTrayIcon,
)

from app.database.schema import STATUS_ACTIVE, STATUS_DONE, STATUS_NOT_DONE
from app.ui.dialogs import AIReviewDialog, NotDoneDialog
from app.ui.main_window import POSTPONE_WARNING, MainWindow
from app.ui.task_widget import TaskWidget


class TestMainWindowCreation:
    def test_window_created(self, make_window):
        w = make_window()
        assert isinstance(w, MainWindow)
        assert w.windowTitle() == "Study Agent"

    def test_loads_today_tasks(self, make_window, task_service):
        task_service.create_task("读书", scheduled_date="2026-01-05")
        task_service.create_task("写代码", scheduled_date="2026-01-05")
        w = make_window()
        assert len(w._task_widgets) == 2
        titles = {wd.task().title for wd in w._task_widgets}
        assert titles == {"读书", "写代码"}

    def test_ignores_tasks_from_other_dates(self, make_window, task_service):
        task_service.create_task("今天", scheduled_date="2026-01-05")
        task_service.create_task("明天", scheduled_date="2026-01-06")
        w = make_window()
        assert len(w._task_widgets) == 1

    def test_empty_hint_when_no_tasks(self, make_window, qtbot):
        w = make_window()
        qtbot.addWidget(w)
        assert w.empty_hint.isHidden() is False
        assert w.scroll.isHidden() is True

    def test_date_label_matches_injected_today(self, make_window):
        w = make_window()
        assert w.date_label.text() == "2026-01-05"

    def test_tray_graceful_when_unavailable(self, make_window):
        # offscreen 平台下托盘可能不可用，但窗口仍能正常创建
        w = make_window()
        assert isinstance(w._tray, (QSystemTrayIcon, type(None)))


class TestCompleteFlow:
    def test_complete_button_calls_service_and_refreshes(
        self, make_window, task_service, qtbot
    ):
        t = task_service.create_task("完成我", scheduled_date="2026-01-05")
        w = make_window()
        qtbot.addWidget(w)

        assert len(w._task_widgets) == 1
        widget = w._task_widgets[0]
        # 点击"完成"
        qtbot.mouseClick(widget.complete_btn, Qt.MouseButton.LeftButton)

        # service 层状态已变
        assert task_service.get_status(t.id) == STATUS_DONE
        # UI 已刷新为 done 状态
        assert len(w._task_widgets) == 1
        assert w._task_widgets[0].task().status == STATUS_DONE
        assert w._task_widgets[0].done_label is not None
        # 统计更新
        assert "完成 1 / 总任务 1" in w.stat_progress.text()


class TestNotDoneDialog:
    def test_empty_reason_not_submittable(self, qtbot):
        dlg = NotDoneDialog("任务A")
        qtbot.addWidget(dlg)
        # 清空原因
        dlg.reason_edit.setPlainText("   ")
        ok_btn = dlg.buttons.button(QDialogButtonBox.StandardButton.Ok)
        qtbot.mouseClick(ok_btn, Qt.MouseButton.LeftButton)
        # 未关闭、提示可见
        assert dlg.result() != QDialog.DialogCode.Accepted
        assert dlg.error_label.isHidden() is False

    def test_valid_reason_submits(self, qtbot):
        dlg = NotDoneDialog("任务A")
        qtbot.addWidget(dlg)
        dlg.reason_edit.setPlainText("今天没时间复习")
        ok_btn = dlg.buttons.button(QDialogButtonBox.StandardButton.Ok)
        qtbot.mouseClick(ok_btn, Qt.MouseButton.LeftButton)
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.reason() == "今天没时间复习"


class TestNotDoneFlow:
    def test_not_done_valid_reason_updates_ui(
        self, make_window, task_service, qtbot, monkeypatch
    ):
        t = task_service.create_task("未完成我", scheduled_date="2026-01-05")
        w = make_window()
        qtbot.addWidget(w)

        # 替换对话框为直接返回固定原因（避免真实弹窗）
        monkeypatch.setattr(
            "app.ui.main_window.NotDoneDialog.get_reason",
            lambda parent, title: "临时有事",
        )
        widget = w._task_widgets[0]
        qtbot.mouseClick(widget.not_done_btn, Qt.MouseButton.LeftButton)

        assert task_service.get_status(t.id) == STATUS_NOT_DONE
        assert task_service.get_details(t.id)["reason"] == "临时有事"
        refreshed = w._task_widgets[0]
        assert refreshed.task().status == STATUS_NOT_DONE
        assert "临时有事" in refreshed.reason_label.text()


class TestPostponeFlow:
    def test_postpone_moves_task_out_of_today_and_refreshes(
        self, make_window, task_service, qtbot
    ):
        # 先走一遍 active -> not_done -> postpone
        t = task_service.create_task("延期我", scheduled_date="2026-01-05")
        task_service.mark_not_done(t.id, "没做完")
        w = make_window()
        qtbot.addWidget(w)

        assert len(w._task_widgets) == 1
        widget = w._task_widgets[0]
        qtbot.mouseClick(widget.postpone_btn, Qt.MouseButton.LeftButton)

        # 任务离开今天
        got = task_service.get_task(t.id)
        assert got.scheduled_date == "2026-01-06"
        assert got.status == STATUS_ACTIVE
        assert got.postpone_count == 1
        # UI：今日列表已无该任务
        assert len(w._task_widgets) == 0
        assert w.empty_hint.isHidden() is False

    def test_postpone_warning_shown_in_card_at_three(
        self, make_window, task_service, fixed_today, repo
    ):
        # 构造一个"今天"的、已延期 3 次的 not_done 任务
        t = task_service.create_task(
            "重复延期", scheduled_date=fixed_today
        )
        repo.update(
            t.id,
            status=STATUS_NOT_DONE,
            reason="一直没完成",
            postpone_count=3,
        )
        got = task_service.get_task(t.id)
        widget = TaskWidget(got)
        assert widget.warning_label.isHidden() is False
        assert POSTPONE_WARNING in widget.warning_label.text()

    def test_postpone_statusbar_warning_at_three(
        self, make_window, task_service, fixed_today, qtbot, repo
    ):
        # postpone_count=2 的任务点击"延期到明天"后达到 3，
        # 状态栏应出现警告文案
        t = task_service.create_task("预警任务", scheduled_date=fixed_today)
        repo.update(t.id, status=STATUS_NOT_DONE, postpone_count=2)
        w = make_window()
        qtbot.addWidget(w)
        assert len(w._task_widgets) == 1
        qtbot.mouseClick(w._task_widgets[0].postpone_btn, Qt.MouseButton.LeftButton)
        got = task_service.get_task(t.id)
        assert got.postpone_count == 3
        assert POSTPONE_WARNING in w.statusBar().currentMessage()


class TestTodayStats:
    def test_stats_correct(self, make_window, task_service):
        # 完成 1 个 30 分钟任务 + 未处理 1 个 40 分钟任务
        a = task_service.create_task(
            "数学", scheduled_date="2026-01-05", estimated_minutes=30
        )
        task_service.create_task(
            "英语", scheduled_date="2026-01-05", estimated_minutes=40
        )
        task_service.complete_task(a.id)
        w = make_window()
        assert w.stat_progress.text() == "完成 1 / 总任务 2"
        assert w.stat_rate.text() == "完成率：50%"
        assert w.stat_estimated.text() == "预计学习时间：70 分钟"
        assert w.stat_done_time.text() == "已完成学习时间：30 分钟"

    def test_stats_empty(self, make_window):
        w = make_window()
        assert w.stat_progress.text() == "完成 0 / 总任务 0"
        assert w.stat_rate.text() == "完成率：0%"


class TestStartupDateProcessing:
    def test_startup_calls_date_transition(self, make_window, date_service, fixed_today):
        # 构造窗口时 should 调用 process_date_transition(today)
        assert date_service.get_last_processed_date() is None
        w = make_window()
        # 首次进入应记录固定日期
        assert date_service.get_last_processed_date() == fixed_today

    def test_startup_brings_postponed_task_to_today(
        self, make_window, task_service, date_service, fixed_today
    ):
        # 昨天的任务被延期到 fixed_today，启动后应出现在今日列表
        yesterday = "2026-01-04"
        t = task_service.create_task("从昨天延期来", scheduled_date=yesterday)
        task_service.mark_not_done(t.id, "没完成")
        # 延期时基于 scheduled_date +1 => 2026-01-05 = fixed_today
        task_service.postpone_task(t.id)
        assert task_service.get_task(t.id).scheduled_date == fixed_today

        # 先让日期服务认识到已处理过 yesterday（模拟昨天运行过）
        date_service.process_date_transition(yesterday)
        w = make_window()
        assert len(w._task_widgets) == 1
        assert w._task_widgets[0].task().id == t.id


class TestAIReviewUnavailable:
    """AI 不可用 / 未配置时，本地功能仍正常（不能崩溃）。"""

    def test_ai_not_configured_status_message(
        self, make_window, task_service, qtbot, monkeypatch
    ):
        task_service.create_task("任务", scheduled_date="2026-01-05")
        w = make_window()  # 未传 review_service => AI 未配置
        qtbot.addWidget(w)
        monkeypatch.setattr(
            "app.ui.main_window.NotDoneDialog.get_reason",
            lambda parent, title: "生病了",
        )
        qtbot.mouseClick(w._task_widgets[0].not_done_btn, Qt.MouseButton.LeftButton)
        # 本地记录 status 不变规则，且不崩溃
        assert task_service.get_task(w._task_widgets[0].task().id).status is not None
        assert "AI 未配置" in w.statusBar().currentMessage()

    def test_local_postpone_still_works_without_ai(
        self, make_window, task_service, qtbot, monkeypatch
    ):
        t = task_service.create_task("任务", scheduled_date="2026-01-05")
        task_service.mark_not_done(t.id, "原因X")
        w = make_window()
        qtbot.addWidget(w)
        assert len(w._task_widgets) == 1
        qtbot.mouseClick(w._task_widgets[0].postpone_btn, Qt.MouseButton.LeftButton)
        # AI 未配置时手动延期仍生效
        got = task_service.get_task(t.id)
        assert got.scheduled_date == "2026-01-06"
        assert got.postpone_count == 1


class _FakeReviewService:
    """Test double：实现 is_configured + review_task。"""

    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = 0

    def is_configured(self):
        return True

    def review_task(self, task, reason, today=None):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


class _BlockingReviewService(_FakeReviewService):
    """模拟慢速 AI，验证后台线程不阻塞主线程。"""

    def __init__(self, result=None, error=None, delay=0.5):
        import time

        time
        super().__init__(result=result, error=error)
        self.delay = delay

    def review_task(self, task, reason, today=None):
        import time

        self.calls += 1
        time.sleep(self.delay)
        if self._error is not None:
            raise self._error
        return self._result


class TestAIReviewDialog:
    def test_shows_postpone_advice_when_true(self, qtbot, task_service):
        from app.ai.schemas import TaskReview

        task = task_service.create_task("任务", scheduled_date="2026-01-05")
        review = TaskReview(
            reasonable=True, score=0.85, should_postpone=True,
            suggested_date="2026-01-06", analysis="突发课程冲突",
            suggestion="建议延期一天",
        )
        dlg = AIReviewDialog(task, show_loading=True)
        qtbot.addWidget(dlg)
        dlg.show_result(review)
        assert "85%" in dlg.reasonable_label.text()
        assert "建议延期：是" in dlg.postpone_label.text()
        assert "2026-01-06" in dlg.suggested_date_label.text()
        assert dlg.has_result() is True

    def test_shows_no_postpone_when_false(self, qtbot, task_service):
        from app.ai.schemas import TaskReview

        task = task_service.create_task("任务", scheduled_date="2026-01-05")
        review = TaskReview(
            reasonable=False, score=0.3, should_postpone=False,
            suggested_date=None, analysis="刷视频导致", suggestion="减少娱乐时间",
        )
        dlg = AIReviewDialog(task)
        qtbot.addWidget(dlg)
        dlg.show_result(review)
        assert "建议延期：否" in dlg.postpone_label.text()
        assert dlg.reasonable_label.text().startswith("合理程度：30%")

    def test_loading_state_initially(self, qtbot, task_service):
        task = task_service.create_task("任务", scheduled_date="2026-01-05")
        dlg = AIReviewDialog(task, show_loading=True)
        qtbot.addWidget(dlg)
        assert "正在分析" in dlg.loading_label.text()
        assert dlg.loading_label.isHidden() is False

    def test_unavailable_shows_error_but_buttons_remain(self, qtbot, task_service):
        task = task_service.create_task("任务", scheduled_date="2026-01-05")
        dlg = AIReviewDialog(task)
        qtbot.addWidget(dlg)
        dlg.show_unavailable("网络错误")
        assert "AI 暂时不可用" in dlg.error_label.text()
        assert dlg.postpone_btn.isEnabled() is True
        assert dlg.no_postpone_btn.isEnabled() is True

    def test_postpone_button_emits_signal(self, qtbot, task_service):
        task = task_service.create_task("任务", scheduled_date="2026-01-05")
        dlg = AIReviewDialog(task)
        qtbot.addWidget(dlg)
        with qtbot.waitSignal(dlg.postpone_requested, timeout=2000):
            qtbot.mouseClick(dlg.postpone_btn, Qt.MouseButton.LeftButton)


class TestAIReviewWorker:
    def test_worker_success_emits_result(self, qtbot, task_service):
        from app.ai.schemas import TaskReview
        from app.ui.ai_worker import AIReviewWorker

        task = task_service.create_task("任务", scheduled_date="2026-01-05")
        review = TaskReview(
            reasonable=True, score=0.8, should_postpone=True,
            suggested_date="2026-01-06", analysis="a", suggestion="b",
        )
        service = _FakeReviewService(result=review)
        worker = AIReviewWorker(service, task, "原因")
        with qtbot.waitSignal(worker.result_ready, timeout=3000) as blocker:
            worker.start()
            qtbot.wait(50)
        got = blocker.args[0]
        assert got.should_postpone is True
        worker.wait(2000)

    def test_worker_failure_emits_failed(self, qtbot, task_service):
        from app.ai.interface import AIServiceError
        from app.ui.ai_worker import AIReviewWorker

        task = task_service.create_task("任务", scheduled_date="2026-01-05")
        service = _FakeReviewService(error=AIServiceError("AI 暂时不可用：boom"))
        worker = AIReviewWorker(service, task, "原因")
        with qtbot.waitSignal(worker.review_failed, timeout=3000) as blocker:
            worker.start()
            qtbot.wait(50)
        assert "boom" in blocker.args[0]
        worker.wait(2000)




class TestAIReviewUnavailable:
    """AI 不可用 / 未配置时，本地功能仍正常（不能崩溃）。"""

    def test_ai_not_configured_status_message(
        self, make_window, task_service, qtbot, monkeypatch
    ):
        task_service.create_task("任务", scheduled_date="2026-01-05")
        w = make_window()  # 未传 review_service => AI 未配置
        qtbot.addWidget(w)
        monkeypatch.setattr(
            "app.ui.main_window.NotDoneDialog.get_reason",
            lambda parent, title: "生病了",
        )
        clicked = w._task_widgets[0]
        qtbot.mouseClick(clicked.not_done_btn, Qt.MouseButton.LeftButton)
        # not_done 状态已保存，且提示 AI 未配置
        tid = clicked.task().id
        assert task_service.get_status(tid) == STATUS_NOT_DONE
        assert "AI 未配置" in w.statusBar().currentMessage()

    def test_local_postpone_still_works_without_ai(
        self, make_window, task_service, qtbot
    ):
        t = task_service.create_task("任务", scheduled_date="2026-01-05")
        task_service.mark_not_done(t.id, "原因X")
        w = make_window()
        qtbot.addWidget(w)
        assert len(w._task_widgets) == 1
        qtbot.mouseClick(w._task_widgets[0].postpone_btn, Qt.MouseButton.LeftButton)
        # AI 未配置时手动延期仍生效
        got = task_service.get_task(t.id)
        assert got.scheduled_date == "2026-01-06"
        assert got.postpone_count == 1


class _FakeReviewService:
    """Test double：实现 TaskReviewService 的 is_configured + review_task。"""

    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = 0

    def is_configured(self):
        return True

    def review_task(self, task, reason, today=None):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


class _BlockingReviewService(_FakeReviewService):
    """模拟慢速 AI，验证后台线程不阻塞主线程。"""

    def __init__(self, result=None, error=None, delay=0.5):
        import time as _time

        self._t = _time
        super().__init__(result=result, error=error)
        self.delay = delay

    def review_task(self, task, reason, today=None):
        self.calls += 1
        self._t.sleep(self.delay)
        if self._error is not None:
            raise self._error
        return self._result


class TestAIReviewDialog:
    def test_shows_postpone_advice_when_true(self, qtbot, task_service):
        from app.ai.schemas import TaskReview

        task = task_service.create_task("任务", scheduled_date="2026-01-05")
        review = TaskReview(
            reasonable=True, score=0.85, should_postpone=True,
            suggested_date="2026-01-06", analysis="突发课程冲突",
            suggestion="建议延期一天",
        )
        dlg = AIReviewDialog(task, show_loading=True)
        qtbot.addWidget(dlg)
        dlg.show_result(review)
        assert "85%" in dlg.reasonable_label.text()
        assert "建议延期：是" in dlg.postpone_label.text()
        assert "2026-01-06" in dlg.suggested_date_label.text()
        assert dlg.has_result() is True

    def test_shows_no_postpone_when_false(self, qtbot, task_service):
        from app.ai.schemas import TaskReview

        task = task_service.create_task("任务", scheduled_date="2026-01-05")
        review = TaskReview(
            reasonable=False, score=0.3, should_postpone=False,
            suggested_date=None, analysis="刷视频导致", suggestion="减少娱乐时间",
        )
        dlg = AIReviewDialog(task)
        qtbot.addWidget(dlg)
        dlg.show_result(review)
        assert "建议延期：否" in dlg.postpone_label.text()
        assert dlg.reasonable_label.text().startswith("合理程度：30%")

    def test_loading_state_initially(self, qtbot, task_service):
        task = task_service.create_task("任务", scheduled_date="2026-01-05")
        dlg = AIReviewDialog(task, show_loading=True)
        qtbot.addWidget(dlg)
        assert "正在分析" in dlg.loading_label.text()
        assert dlg.loading_label.isHidden() is False

    def test_unavailable_shows_error_but_buttons_remain(self, qtbot, task_service):
        task = task_service.create_task("任务", scheduled_date="2026-01-05")
        dlg = AIReviewDialog(task)
        qtbot.addWidget(dlg)
        dlg.show_unavailable("网络错误")
        assert "AI 暂时不可用" in dlg.error_label.text()
        assert dlg.postpone_btn.isEnabled() is True
        assert dlg.no_postpone_btn.isEnabled() is True

    def test_postpone_button_emits_signal(self, qtbot, task_service):
        task = task_service.create_task("任务", scheduled_date="2026-01-05")
        dlg = AIReviewDialog(task)
        qtbot.addWidget(dlg)
        with qtbot.waitSignal(dlg.postpone_requested, timeout=2000):
            qtbot.mouseClick(dlg.postpone_btn, Qt.MouseButton.LeftButton)


class TestAIReviewWorker:
    def test_worker_success_emits_result(self, qtbot, task_service):
        from app.ai.schemas import TaskReview
        from app.ui.ai_worker import AIReviewWorker

        task = task_service.create_task("任务", scheduled_date="2026-01-05")
        review = TaskReview(
            reasonable=True, score=0.8, should_postpone=True,
            suggested_date="2026-01-06", analysis="a", suggestion="b",
        )
        service = _FakeReviewService(result=review)
        worker = AIReviewWorker(service, task, "原因")
        with qtbot.waitSignal(worker.result_ready, timeout=3000) as blocker:
            worker.start()
            qtbot.wait(50)
        got = blocker.args[0]
        assert got.should_postpone is True
        worker.wait(2000)

    def test_worker_failure_emits_failed(self, qtbot, task_service):
        from app.ai.interface import AIServiceError
        from app.ui.ai_worker import AIReviewWorker

        task = task_service.create_task("任务", scheduled_date="2026-01-05")
        service = _FakeReviewService(error=AIServiceError("AI 暂时不可用：boom"))
        worker = AIReviewWorker(service, task, "原因")
        with qtbot.waitSignal(worker.review_failed, timeout=3000) as blocker:
            worker.start()
            qtbot.wait(50)
        assert "boom" in blocker.args[0]
        worker.wait(2000)


class TestAIReviewFlow:
    def test_async_does_not_block_main_thread(
        self, make_window, task_service, qtbot
    ):
        """AI 慢速返回时，主线程事件仍可被处理（不阻塞的直接证据）。"""
        from app.ai.schemas import TaskReview
        from app.ui.ai_worker import AIReviewWorker

        task = task_service.create_task("不阻塞验证", scheduled_date="2026-01-05")
        review = TaskReview(
            reasonable=True, score=0.9, should_postpone=True,
            suggested_date="2026-01-06", analysis="合理", suggestion="延期",
        )
        slow = _BlockingReviewService(result=review, delay=0.4)
        worker = AIReviewWorker(slow, task, "原因")
        worker.start()
        # 主线程在 AI 运行期间仍可正常工作（仅等待 100ms）
        qtbot.wait(100)
        assert worker.isRunning() is True   # AI 还在后台跑
        assert task_service.get_task(task.id) is not None
        qtbot.waitUntil(lambda: not worker.isRunning(), timeout=4000)
        worker.wait(2000)

    def test_full_flow_dialog_shows_ai_advice(
        self, make_window, task_service, qtbot, monkeypatch
    ):
        """e2e：标记未完成 -> 后台 AI -> 对话框展示建议延期。"""
        from app.ai.schemas import TaskReview

        task_service.create_task("任务", scheduled_date="2026-01-05")
        review = TaskReview(
            reasonable=True, score=0.75, should_postpone=True,
            suggested_date="2026-01-06", analysis="时间冲突", suggestion="延期",
        )
        svc = _FakeReviewService(result=review)

        # 构造带 AI 的窗口（使用与 make_window 相同的固定日期服务）
        w = MainWindow(
            task_service=task_service,
            date_service=date_service_for_test(task_service),
            today_provider=lambda: "2026-01-05",
            review_service=svc,
        )
        qtbot.addWidget(w)
        monkeypatch.setattr(
            "app.ui.main_window.NotDoneDialog.get_reason",
            lambda parent, title: "时间冲突",
        )
        qtbot.mouseClick(w._task_widgets[0].not_done_btn, Qt.MouseButton.LeftButton)

        # 等待 AI 对话框出现并展示结果
        qtbot.waitUntil(lambda: _find_ai_dialog(w) is not None, timeout=4000)
        dlg = _find_ai_dialog(w)
        qtbot.waitUntil(lambda: dlg.has_result(), timeout=4000)
        assert "建议延期：是" in dlg.postpone_label.text()
        assert svc.calls == 1
        dlg.close()

    def test_full_flow_ai_failure_shows_unavailable(
        self, make_window, task_service, qtbot, monkeypatch
    ):
        from app.ai.interface import AIServiceError

        task_service.create_task("任务", scheduled_date="2026-01-05")
        svc = _FakeReviewService(error=AIServiceError("请求超时"))
        w = MainWindow(
            task_service=task_service,
            date_service=date_service_for_test(task_service),
            today_provider=lambda: "2026-01-05",
            review_service=svc,
        )
        qtbot.addWidget(w)
        monkeypatch.setattr(
            "app.ui.main_window.NotDoneDialog.get_reason",
            lambda parent, title: "时间冲突",
        )
        qtbot.mouseClick(w._task_widgets[0].not_done_btn, Qt.MouseButton.LeftButton)

        qtbot.waitUntil(lambda: _find_ai_dialog(w) is not None, timeout=4000)
        dlg = _find_ai_dialog(w)
        qtbot.waitUntil(
            lambda: "AI 暂时不可用" in dlg.error_label.text(), timeout=4000
        )
        # 失败后手动延期仍可用
        assert dlg.postpone_btn.isEnabled() is True
        dlg.close()

    def test_review_dialog_postpone_button_postpones(
        self, make_window, task_service, qtbot, fixed_today
    ):
        # 验证对话框的"延期到明天"按钮通过信号驱动业务（此处直接验证信号）
        t = task_service.create_task("任务", scheduled_date=fixed_today)
        task_service.mark_not_done(t.id, "原因")
        got = task_service.get_task(t.id)
        dlg = AIReviewDialog(got)
        qtbot.addWidget(dlg)
        captured = []

        def on_postpone(task_id):
            captured.append(task_id)

        dlg.postpone_requested.connect(on_postpone)
        qtbot.mouseClick(dlg.postpone_btn, Qt.MouseButton.LeftButton)
        assert captured == [t.id]


def date_service_for_test(task_service):
    """复用与 make_window 相同构造的 DateService。"""
    from app.database.repository import TaskRepository
    from app.services.date_service import DateService

    # task_service 内部持有 repo，直接复用同一 repo 构造 DateService
    return DateService(task_service.repo)


def _find_ai_dialog(window):
    """在窗口的顶级子对话框中查找 AIReviewDialog。"""
    from app.ui.dialogs import AIReviewDialog
    from PySide6.QtWidgets import QApplication

    for w in QApplication.topLevelWidgets():
        if isinstance(w, AIReviewDialog) and w.isVisible():
            return w
    return None


class TestStudyPhaseDisplay:
    def test_phase_info_shown_when_service_injected(
        self, qtbot, repo, task_service, date_service, fixed_today
    ):
        """主窗口顶部显示当前阶段与今日学习目标。"""
        from app.database.study_plan_repository import StudyPlanRepository
        from app.services.study_plan_service import StudyPlanService

        sps = StudyPlanService(repo, StudyPlanRepository(repo.conn))
        sps.ensure_default_plan()
        w = MainWindow(
            task_service=task_service,
            date_service=date_service,
            today_provider=lambda: "2026-09-04",  # Phases 1 期间
            study_plan_service=sps,
        )
        qtbot.addWidget(w)
        assert "Python + Linux + Git" in w.phase_label.text()
        assert w.phase_goal_label.text().startswith("今日学习目标")
        assert w.phase_container.isHidden() is False

    def test_phase_area_hidden_without_service(self, make_window, qtbot):
        w = make_window()
        qtbot.addWidget(w)
        assert w.phase_container.isHidden() is True

    def test_phase_info_outside_plan(self, qtbot, repo, task_service, date_service):
        from app.database.study_plan_repository import StudyPlanRepository
        from app.services.study_plan_service import StudyPlanService

        sps = StudyPlanService(repo, StudyPlanRepository(repo.conn))
        sps.ensure_default_plan()
        w = MainWindow(
            task_service=task_service,
            date_service=date_service,
            today_provider=lambda: "2028-06-01",  # 计划期之外
            study_plan_service=sps,
        )
        qtbot.addWidget(w)
        assert "未处于计划期内" in w.phase_label.text()


class TestAIPlannerGUI:
    """AI 今日规划区域：状态显示 / 重新规划 / 保护已完成与延期任务。"""

    def _make_planning_window(
        self, qtbot, repo, task_service, date_service, fixed_today, fake_planner
    ):
        """构造带 daily_planner_service 的主窗口。"""
        from app.database.study_plan_repository import StudyPlanRepository
        from app.services.daily_planner_service import DailyPlannerService
        from app.services.study_plan_service import StudyPlanService

        sps = StudyPlanService(repo, StudyPlanRepository(repo.conn))
        sps.ensure_default_plan()
        planner_service = DailyPlannerService(
            repo, StudyPlanRepository(repo.conn), planner=fake_planner,
            study_plan_service=sps,
        )
        w = MainWindow(
            task_service=task_service,
            date_service=date_service,
            today_provider=lambda: fixed_today,
            study_plan_service=sps,
            daily_planner_service=planner_service,
        )
        return w

    def test_ai_enabled_status(self, qtbot, repo, task_service, date_service,
                               fixed_today):
        class ConfigFakePlanner:
            def is_configured(self):
                return True

        w = self._make_planning_window(
            qtbot, repo, task_service, date_service, fixed_today, ConfigFakePlanner()
        )
        qtbot.addWidget(w)
        assert "AI 已启用" in w.planner_status_label.text()
        assert w.planner_replan_btn.isEnabled() is True

    def test_ai_not_configured_status(self, qtbot, repo, task_service,
                                      date_service, fixed_today):
        class NotConfiguredPlanner:
            def is_configured(self):
                return False

        w = self._make_planning_window(
            qtbot, repo, task_service, date_service, fixed_today,
            NotConfiguredPlanner(),
        )
        qtbot.addWidget(w)
        assert "AI 不可用" in w.planner_status_label.text()
        assert w.planner_replan_btn.isEnabled() is False

    def test_ai_area_hidden_without_service(self, make_window, qtbot):
        w = make_window()
        qtbot.addWidget(w)
        assert w.planner_container.isHidden() is True

    def test_replan_confirmation_and_done_protected(
        self, qtbot, repo, task_service, date_service, fixed_today, monkeypatch
    ):
        """重新规划：确认框被拒绝时不改动；已完成任务永不被删。"""
        from app.database.study_plan_repository import StudyPlanRepository
        from app.services.daily_planner_service import DailyPlannerService
        from app.services.study_plan_service import StudyPlanService

        sps = StudyPlanService(repo, StudyPlanRepository(repo.conn))
        sps.ensure_default_plan()

        class ConfigFakePlanner:
            def is_configured(self):
                return True

        # 先造一个今日已完成任务 + 一个今日 generated 任务
        done = task_service.create_task(
            "已完成任务", scheduled_date=fixed_today, source="manual"
        )
        task_service.complete_task(done.id)
        gen = task_service.create_task(
            "待重排任务", scheduled_date=fixed_today, source="generated"
        )

        planner_service = DailyPlannerService(
            repo, StudyPlanRepository(repo.conn), planner=ConfigFakePlanner(),
            study_plan_service=sps,
        )
        w = MainWindow(
            task_service=task_service,
            date_service=date_service,
            today_provider=lambda: fixed_today,
            study_plan_service=sps,
            daily_planner_service=planner_service,
        )
        qtbot.addWidget(w)

        # 确认框：回答 No（不重新规划）
        monkeypatch.setattr(
            "PySide6.QtWidgets.QMessageBox.question",
            lambda *a, **k: QMessageBox.StandardButton.No,
        )
        w._on_replan()
        # 已完成任务与 generated 任务都保留
        assert task_service.get_task(done.id) is not None
        assert task_service.get_task(gen.id) is not None
        assert task_service.get_status(done.id) == "done"

    def test_replan_only_removes_active_generated_and_keeps_done(
        self, qtbot, repo, task_service, date_service, fixed_today, monkeypatch
    ):
        """确认后：仅 active+generated 被替换，done/manual 保留。"""
        from app.database.study_plan_repository import StudyPlanRepository
        from app.services.daily_planner_service import DailyPlannerService
        from app.services.study_plan_service import StudyPlanService

        sps = StudyPlanService(repo, StudyPlanRepository(repo.conn))
        sps.ensure_default_plan()

        class ConfigFakePlanner:
            def is_configured(self):
                return True
            def plan_next_day(self, context):
                from app.ai.schemas import DailyPlan, RecommendedTask
                return DailyPlan(
                    reasoning="r", adjustment="a",
                    recommended_tasks=(
                        RecommendedTask(topic_id=1, title="新任务",
                                        estimated_minutes=30, priority=2),
                    ),
                    carry_over_tasks=(), daily_minutes=30,
                )

        done = task_service.create_task(
            "已完成", scheduled_date=fixed_today, source="manual"
        )
        task_service.complete_task(done.id)
        gen = task_service.create_task(
            "将被重排", scheduled_date=fixed_today, source="generated"
        )
        manual = task_service.create_task(
            "手动任务", scheduled_date=fixed_today, source="manual"
        )

        planner_service = DailyPlannerService(
            repo, StudyPlanRepository(repo.conn), planner=ConfigFakePlanner(),
            study_plan_service=sps,
        )
        w = MainWindow(
            task_service=task_service,
            date_service=date_service,
            today_provider=lambda: fixed_today,
            study_plan_service=sps,
            daily_planner_service=planner_service,
        )
        qtbot.addWidget(w)

        monkeypatch.setattr(
            "PySide6.QtWidgets.QMessageBox.question",
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )
        w._on_replan()

        # 已完成保留
        assert task_service.get_status(done.id) == "done"
        # 手动任务保留
        assert task_service.repo.get(manual.id) is not None
        # active+generated 被删除
        assert task_service.repo.get(gen.id) is None


class TestSummaryPagesGUI:
    """周/月总结页面：导航、本地统计展示、AI 不可用降级。"""

    def _make_summary_window(
        self, qtbot, repo, task_service, date_service, fixed_today, client=None
    ):
        from app.ai.summary import AISummaryGenerator
        from app.database.study_plan_repository import SummaryCacheRepository
        from app.services.stats_service import StatsService
        from app.services.summary_service import SummaryService

        ai_gen = AISummaryGenerator(client) if client is not None else None
        summary_svc = SummaryService(
            StatsService(repo), SummaryCacheRepository(repo.conn), ai_gen
        )
        w = MainWindow(
            task_service=task_service,
            date_service=date_service,
            today_provider=lambda: fixed_today,
            summary_service=summary_svc,
        )
        return w

    def test_weekly_page_navigation_and_stats(self, qtbot, repo, task_service,
                                              date_service, fixed_today):
        # 周一到今日(2026-01-05 是周一)造任务
        t = task_service.create_task("任务", scheduled_date=fixed_today)
        task_service.complete_task(t.id)
        w = self._make_summary_window(qtbot, repo, task_service, date_service,
                                      fixed_today)
        qtbot.addWidget(w)

        # 默认在今日页
        assert w.stack.currentIndex() == 0
        qtbot.mouseClick(w.nav_weekly_btn, Qt.MouseButton.LeftButton)
        assert w.stack.currentIndex() == 1
        # 页面存在且显示总任务
        page = w.weekly_page
        assert page is not None
        # AI 未配置：显示本地统计 + 不可用提示
        qtbot.waitUntil(lambda: page.category_label is not None, timeout=2000)
        assert "总任务" in page.summary_grid.layout().itemAt(0).widget().text()

    def test_monthly_page_navigation(self, qtbot, repo, task_service,
                                     date_service, fixed_today):
        # fixed_today=2026-01-05 => 2026 年 1 月
        t = task_service.create_task("任务", scheduled_date="2026-01-10")
        task_service.complete_task(t.id)
        w = self._make_summary_window(qtbot, repo, task_service, date_service,
                                      fixed_today)
        qtbot.addWidget(w)
        qtbot.mouseClick(w.nav_monthly_btn, Qt.MouseButton.LeftButton)
        assert w.stack.currentIndex() == 2
        page = w.monthly_page
        assert page is not None
        assert page.year == 2026 and page.month == 1
        # 有"总任务"文本
        texts = []
        for i in range(page.summary_layout.count()):
            item = page.summary_layout.itemAt(i)
            if item.widget() is not None:
                texts.append(item.widget().text())
        assert any("总任务：1" in t0 for t0 in texts)

    def test_summary_pages_disabled_without_service(
        self, make_window, qtbot
    ):
        w = make_window()  # 无 summary_service
        qtbot.addWidget(w)
        assert w.nav_weekly_btn.isEnabled() is False
        assert w.nav_monthly_btn.isEnabled() is False

    def test_ai_unavailable_shows_local_stats(
        self, qtbot, repo, task_service, date_service, fixed_today
    ):
        """AI 不可用（FakeClient configured=False）时仍显示本地统计。"""
        from app.ai.interface import AIServiceError

        class UnconfiguredClient:
            def is_configured(self):
                return False
            def chat(self, *a, **k):
                raise AIServiceError("未配置")

        t = task_service.create_task("任务", scheduled_date=fixed_today)
        task_service.complete_task(t.id)
        w = self._make_summary_window(qtbot, repo, task_service, date_service,
                                      fixed_today, client=UnconfiguredClient())
        qtbot.addWidget(w)
        qtbot.mouseClick(w.nav_weekly_btn, Qt.MouseButton.LeftButton)
        page = w.weekly_page
        qtbot.waitUntil(
            lambda: page.period_label.text() != "", timeout=3000
        )
        # 本地统计仍显示，AI 提示不可用
        assert page.category_label is not None
        first = page.summary_grid.layout().itemAt(0).widget().text()
        assert "总任务" in first


class TestDateInjectionInGUI:
    """GUI 通过 today_provider 注入固定日期（不依赖系统日期）。"""

    def test_main_window_uses_injected_date_label(
        self, make_window, qtbot, fixed_today
    ):
        w = make_window()
        qtbot.addWidget(w)
        assert w.date_label.text() == fixed_today

    def test_default_today_provider_follows_date_utils(
        self, qtbot, repo, task_service, date_service
    ):
        """未显式传 today_provider 时，GUI 跟随 date_utils 的今天（可被注入覆盖）。"""
        from app.utils import date_utils

        original = date_utils.today()
        try:
            date_utils.set_today_provider("2026-09-05")
            w = MainWindow(
                task_service=task_service,
                date_service=date_service,
            )
            qtbot.addWidget(w)
            assert w.date_label.text() == "2026-09-05"
        finally:
            date_utils.reset_today_provider()
            assert date_utils.today() == original
