"""主窗口。

职责：
- 组装 UI（标题栏、今日任务列表、统计栏）；
- 作为 UI 层唯一与 service 交互的入口，将 TaskWidget 的操作信号
  转成对 TaskService / DateService 的调用；
- 启动时执行日期切换检查；
- 基础系统托盘（平台不支持时自动降级，不影响运行）。
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ..database.schema import STATUS_DONE
from ..services.date_service import DateService
from ..services.task_review_service import TaskReviewService
from ..services.task_service import TaskService
from ..utils.date_utils import add_days, today as _default_today
from .ai_worker import AIReviewWorker
from .dialogs import AIReviewDialog, NotDoneDialog
from .styles import APP_STYLE
from .task_widget import TaskWidget

POSTPONE_WARNING = "该任务已经连续延期 3 次，请考虑拆分任务或调整计划。"


def _tray_icon() -> QIcon:
    """生成一个简单的程序图标（托盘 / 窗口通用）。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    from PySide6.QtGui import QColor, QPainter

    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#2c6fbb"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
    painter.setPen(QColor("white"))
    painter.setFont(painter.font())
    painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "学")
    painter.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    def __init__(
        self,
        task_service: TaskService,
        date_service: DateService,
        today_provider=None,
        review_service: TaskReviewService | None = None,
        study_plan_service=None,
        daily_planner_service=None,
        summary_service=None,
    ):
        super().__init__()
        self.task_service = task_service
        self.date_service = date_service
        self.today_provider = today_provider or _default_today
        # AI 复核服务：可选，未配置/未传时本地功能完全正常
        self.review_service = review_service
        # 学习计划服务：可选，用于显示当前阶段；未传则隐藏该区域
        self.study_plan_service = study_plan_service
        # AI 动态规划服务：可选，用于”AI 今日规划“区域；未传则显示不可用
        self.daily_planner_service = daily_planner_service
        # 周/月总结服务：可选；未传则隐藏周/月总结页
        self.summary_service = summary_service

        self._task_widgets: list[TaskWidget] = []
        self._quit_requested = False
        self._tray: QSystemTrayIcon | None = None
        self._ai_workers: list[AIReviewWorker] = []

        self.setWindowTitle("Study Agent")
        self.setMinimumSize(560, 460)
        self.resize(800, 650)
        self.setWindowIcon(_tray_icon())

        self._build_ui()
        self._build_tray()
        self._apply_styles()
        self._on_startup()

    # ---------- UI 构建 ----------

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(8)

        # 顶部导航 [今日] [周总结] [月总结]
        nav = QHBoxLayout()
        self.nav_today_btn = QPushButton("今日")
        self.nav_weekly_btn = QPushButton("周总结")
        self.nav_monthly_btn = QPushButton("月总结")
        self.nav_today_btn.clicked.connect(lambda: self._switch_page(0))
        self.nav_weekly_btn.clicked.connect(lambda: self._switch_page(1))
        self.nav_monthly_btn.clicked.connect(lambda: self._switch_page(2))
        for b in (self.nav_today_btn, self.nav_weekly_btn, self.nav_monthly_btn):
            b.setObjectName("PrimaryButton")
            nav.addWidget(b)
        nav.addStretch()
        root.addLayout(nav)

        # 页面栈：0=今日 1=周总结 2=月总结
        self.stack = QStackedWidget()
        root.addWidget(self.stack, stretch=1)

        # ----- 今日页 -----
        today_page = QWidget()
        root_today = QVBoxLayout(today_page)
        root_today.setContentsMargins(4, 0, 4, 0)
        root_today.setSpacing(10)

        # 顶部标题 + 日期
        self.title_label = QLabel("Study Agent")
        self.title_label.setObjectName("AppTitle")
        root_today.addWidget(self.title_label)

        self.date_label = QLabel("")
        self.date_label.setObjectName("AppDate")
        root_today.addWidget(self.date_label)

        section = QLabel("今日学习任务")
        section.setObjectName("SectionTitle")
        root_today.addWidget(section)

        # 当前学习阶段（StudyPlanService 可选注入；不注入则隐藏）
        self.phase_container = QWidget()
        phase_box = QVBoxLayout(self.phase_container)
        phase_box.setContentsMargins(0, 0, 0, 0)
        phase_box.setSpacing(2)
        self.phase_label = QLabel("")
        self.phase_label.setObjectName("TaskMeta")
        self.phase_goal_label = QLabel("")
        self.phase_goal_label.setObjectName("TaskMeta")
        phase_box.addWidget(self.phase_label)
        phase_box.addWidget(self.phase_goal_label)
        root_today.addWidget(self.phase_container)

        # AI 今日规划区域（DailyPlannerService 可选注入；不注入则隐藏）
        self.planner_container = QWidget()
        planner_box = QVBoxLayout(self.planner_container)
        planner_box.setContentsMargins(0, 0, 0, 0)
        planner_box.setSpacing(4)
        self.planner_status_label = QLabel("AI 状态：AI 不可用")
        self.planner_status_label.setObjectName("TaskMeta")
        self.planner_note_label = QLabel(
            "今日计划由 AI 根据最近 7 天学习情况调整"
        )
        self.planner_note_label.setObjectName("TaskMeta")
        self.planner_replan_btn = QPushButton("重新规划今天")
        self.planner_replan_btn.clicked.connect(self._on_replan)
        self.planner_row = QHBoxLayout()
        self.planner_row.setSpacing(10)
        self.planner_row.addWidget(self.planner_status_label)
        self.planner_row.addWidget(self.planner_note_label)
        self.planner_row.addStretch()
        self.planner_row.addWidget(self.planner_replan_btn)
        planner_box.addLayout(self.planner_row)
        root_today.addWidget(self.planner_container)

        # 滚动任务列表
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setStyleSheet("background: transparent;")
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch()
        self.scroll.setWidget(self.list_container)
        root_today.addWidget(self.scroll, stretch=1)

        # 空状态提示
        self.empty_hint = QLabel("今天还没有学习任务。")
        self.empty_hint.setObjectName("EmptyHint")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root_today.addWidget(self.empty_hint)

        # 今日统计栏
        stats = QWidget()
        stats.setObjectName("StatsBar")
        sbox = QVBoxLayout(stats)
        sbox.setContentsMargins(14, 10, 14, 10)
        sbox.setSpacing(6)
        self.stats_title = QLabel("今日进度")
        self.stats_title.setObjectName("StatsTitle")
        sbox.addWidget(self.stats_title)

        self.stat_progress = QLabel("完成 0 / 总任务 0")
        self.stat_rate = QLabel("完成率：0%")
        self.stat_estimated = QLabel("预计学习时间：0 分钟")
        self.stat_done_time = QLabel("已完成学习时间：0 分钟")
        for lbl in (
            self.stat_progress,
            self.stat_rate,
            self.stat_estimated,
            self.stat_done_time,
        ):
            lbl.setObjectName("StatsValue")
            sbox.addWidget(lbl)
        root_today.addWidget(stats)

        self.stack.addWidget(today_page)

        # ----- 周总结页 / 月总结页（可选） -----
        if self.summary_service is not None:
            from .summary_pages import MonthlySummaryPage, WeeklySummaryPage

            self.weekly_page = WeeklySummaryPage(
                self.summary_service, today_provider=self.today_provider
            )
            self.monthly_page = MonthlySummaryPage(
                self.summary_service, today_provider=self.today_provider
            )
            self.stack.addWidget(self.weekly_page)
            self.stack.addWidget(self.monthly_page)
            self.nav_weekly_btn.setEnabled(True)
            self.nav_monthly_btn.setEnabled(True)
        else:
            self.nav_weekly_btn.setEnabled(False)
            self.nav_monthly_btn.setEnabled(False)

        self.setCentralWidget(central)
        self.statusBar().showMessage("")

    def _switch_page(self, index: int) -> None:
        """切换今日/周/月页面。"""
        if self.summary_service is None and index != 0:
            self.statusBar().showMessage("周/月总结不可用", 3000)
            return
        self.stack.setCurrentIndex(index)

    def _build_tray(self) -> None:
        """托盘可用则创建，不可用（如部分 Linux）则跳过，不影响运行。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = None
            return
        self._tray = QSystemTrayIcon(_tray_icon(), self)
        self._tray.setToolTip("Study Agent")

        menu = QMenu(self)
        open_action = QAction("打开", self)
        open_action.triggered.connect(self._restore_from_tray)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(open_action)
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)

        self._tray.activated.connect(
            lambda reason: (
                self._restore_from_tray()
                if reason == QSystemTrayIcon.ActivationReason.Trigger
                else None
            )
        )
        self._tray.show()

    def _apply_styles(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(APP_STYLE)

    # ---------- 启动 / 刷新 ----------

    def _on_startup(self) -> None:
        """启动流程：先做日期切换，再加载今日任务。"""
        today_str = self.today_provider()
        self.date_service.process_date_transition(today_str)
        self.current_date = today_str
        self.date_label.setText(today_str)
        self.refresh()

    def refresh(self) -> None:
        """重建今日任务列表并刷新统计。"""
        today_str = self.current_date
        tasks = self.task_service.get_tasks_by_date(today_str)

        # 当前学习阶段显示
        self._update_phase_info(today_str)
        # AI 今日规划状态
        self._update_planner_info()

        # 清空旧卡片
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._task_widgets.clear()

        for task in tasks:
            widget = TaskWidget(task)
            widget.complete_requested.connect(self._on_complete)
            widget.not_done_requested.connect(self._on_not_done)
            widget.postpone_requested.connect(self._on_postpone)
            self.list_layout.addWidget(widget)
            self._task_widgets.append(widget)

        self.empty_hint.setVisible(len(tasks) == 0)
        self.scroll.setVisible(len(tasks) > 0)
        self._refresh_stats(today_str)

    def _update_phase_info(self, today_str: str) -> None:
        """显示当前学习阶段与今日学习目标。"""
        if self.study_plan_service is None:
            self.phase_container.setVisible(False)
            return
        phase = self.study_plan_service.get_current_phase(today_str)
        if phase is None:
            self.phase_container.setVisible(True)
            self.phase_label.setText("当前阶段：未处于计划期内")
            self.phase_goal_label.setText("")
            return
        self.phase_container.setVisible(True)
        self.phase_label.setText(f"当前阶段：{phase.name}")
        goal = (phase.goals or "").strip()
        self.phase_goal_label.setText(f"今日学习目标：{goal}" if goal else "")

    def _update_planner_info(self) -> None:
        """刷新 AI 今日规划区域的可用状态。"""
        if self.daily_planner_service is None:
            self.planner_container.setVisible(False)
            return
        planner = self.daily_planner_service.planner
        if planner is not None and planner.is_configured():
            self.planner_status_label.setText("AI 状态：AI 已启用")
            self.planner_replan_btn.setEnabled(True)
        else:
            self.planner_status_label.setText("AI 状态：AI 不可用")
            self.planner_replan_btn.setEnabled(False)
        self.planner_container.setVisible(True)

    # ---------- AI 重新规划 ----------

    def _on_replan(self) -> None:
        """重新规划今天：仅清理 active 且 source='generated' 的任务后重新生成。

        保护：
        - 已完成任务（done）不动
        - 已标记未完成（not_done）不动
        - 延期历史任务不动（不从这里删除）；仅把它们从今日列表中排除
        - 手动创建的任务（source='manual'）不动
        """
        from .dialogs import show_warning

        # 确认框
        if self.daily_planner_service is None:
            show_warning(self, "AI 规划不可用，无法重新规划。")
            return
        planner = self.daily_planner_service.planner
        if planner is None or not planner.is_configured():
            show_warning(self, "AI 未配置，无法重新规划。")
            return

        confirm = QMessageBox.question(
            self,
            "重新规划",
            "重新规划可能改变未开始任务。\n已完成与延期任务不受影响，确定继续？",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        # 清理今天可被重排的普通生成任务（active + generated）
        today_str = self.current_date
        tasks = self.task_service.get_tasks_by_date(today_str)
        cleaned_ids = []
        for t in tasks:
            if t.status == "active" and t.source == "generated":
                self.task_service.repo.delete(t.id)
                cleaned_ids.append(t.id)

        # 对该日期重新生成（AI 优先，内部自动 fallback）
        result = self.daily_planner_service.generate_next_day_plan(
            add_days(today_str, -1)
        )
        self.refresh()
        msg = f"重新规划完成：生成了 {len(result.get('created', []))} 个任务"
        if cleaned_ids:
            msg += f"，移除了 {len(cleaned_ids)} 个旧生成任务"
        self.statusBar().showMessage(msg, 5000)

    def _refresh_stats(self, today_str: str) -> None:
        stats = self.task_service.get_daily_stats(today_str)
        time_stats = self.task_service.get_study_time_stats(today_str)
        self.stat_progress.setText(
            f"完成 {stats['done']} / 总任务 {stats['total']}"
        )
        self.stat_rate.setText(f"完成率：{stats['rate']:.0f}%")
        self.stat_estimated.setText(
            f"预计学习时间：{time_stats['total_minutes']} 分钟"
        )
        self.stat_done_time.setText(
            f"已完成学习时间：{time_stats['done_minutes']} 分钟"
        )

    # ---------- 操作处理 ----------

    def _on_complete(self, task_id: int) -> None:
        self.task_service.complete_task(task_id)
        self.refresh()
        self.statusBar().showMessage("任务已完成", 3000)

    def _on_not_done(self, task_id: int) -> None:
        """未完成：先保存原因，再异步请求 AI 复核；AI 不可用不影响本地流程。"""
        task = self.task_service.get_task(task_id)
        reason = NotDoneDialog.get_reason(self, task.title)
        if reason is None:
            return  # 用户取消
        # 原因已保证非空（对话框内校验）
        self.task_service.mark_not_done(task_id, reason)
        self.refresh()
        self.statusBar().showMessage("已记录未完成原因", 3000)

        task = self.task_service.get_task(task_id)
        if self.review_service is None or not self.review_service.is_configured():
            self.statusBar().showMessage("AI 未配置，请手动决定是否延期。", 5000)
            return

        # 非阻塞：后台线程分析，弹出结果对话框
        dialog = AIReviewDialog(task, parent=self)
        dialog.postpone_requested.connect(self._on_dialog_postpone)
        dialog.no_postpone_requested.connect(self._on_dialog_no_postpone)
        dialog.show()

        worker = AIReviewWorker(
            self.review_service,
            task,
            task.reason or "",
            today=self.current_date,
            parent=self,
        )
        worker.result_ready.connect(lambda review, d=dialog: d.show_result(review))
        worker.review_failed.connect(lambda msg, d=dialog: d.show_unavailable(msg))
        worker.finished.connect(
            lambda w=worker: self._release_worker(w)
        )
        self._ai_workers.append(worker)
        worker.start()

    def _release_worker(self, worker: AIReviewWorker) -> None:
        """AI 线程结束后从列表中移除引用。"""
        if worker in self._ai_workers:
            self._ai_workers.remove(worker)

    def _on_dialog_postpone(self, task_id: int) -> None:
        """用户在 AI 结果对话框中点击"延期到明天"。"""
        self._on_postpone(task_id)

    def _on_dialog_no_postpone(self, task_id: int) -> None:
        """用户选择不延期：保持 not_done，刷新界面。"""
        self.refresh()
        self.statusBar().showMessage("已保持未完成状态", 3000)


    def _on_postpone(self, task_id: int) -> None:
        task = self.task_service.postpone_task(task_id)
        self.refresh()
        self.statusBar().showMessage(
            f"已延期到 {task.scheduled_date}", 3000
        )
        if task.postpone_count >= 3:
            self.statusBar().showMessage(
                f"已延期到 {task.scheduled_date}。{POSTPONE_WARNING}", 8000
            )

    # ---------- 托盘 / 关闭 ----------

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        """托盘“退出”：关闭主窗口（closeEvent 负责清理与退出）。"""
        if self._tray is not None:
            self._tray.hide()
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        """点右上角 X / 托盘“退出” = 真正退出程序，不驻留后台。

        顺序：清理托盘 -> 安全停止并等待 AI worker -> 关闭 AI 对话框，
        然后接受关闭；QApplication 因“最后一个窗口已关闭”而正常退出，
        PowerShell 立即返回提示符。
        """
        if not self._quit_requested:
            self._shutdown()
        event.accept()

    def _shutdown(self) -> None:
        """退出前集中清理：托盘、AI 线程、打开的 AI 对话框。"""
        self._quit_requested = True

        # 1) 清理托盘（隐藏 + 删除），不再驻留后台
        if self._tray is not None:
            self._tray.hide()
            self._tray.deleteLater()
            self._tray = None

        # 2) 安全停止 AI worker：请求中断并等待线程真正结束，
        #    避免 “QThread: Destroyed while thread is still running”。
        self._stop_ai_workers()

        # 3) 关闭可能仍打开着的 AI 结果对话框，避免残留顶级窗口
        #    （否则“最后一个窗口关闭”不会触发，QApplication 不退出）。
        app = QApplication.instance()
        if app is not None:
            for w in list(app.topLevelWidgets()):
                if isinstance(w, AIReviewDialog) and w.isVisible():
                    w.close()

    def _stop_ai_workers(self) -> None:
        """请求所有 AI worker 停止并等待其线程结束，之后释放引用。"""
        workers = list(self._ai_workers)
        self._ai_workers.clear()
        for w in workers:
            if w is None:
                continue
            if w.isRunning():
                # 协作式中断；run() 里的网络调用最终会超时返回，
                # wait() 保证线程结束前不会被销毁。
                w.requestInterruption()
                w.wait()
            w.deleteLater()

    def run_app(self) -> int:
        """显示窗口并进入事件循环（app/main.py 使用）。"""
        self.show()
        return QApplication.instance().exec()

    @staticmethod
    def main() -> int:
        """便捷入口：从零启动整个应用（仅供调试/快速运行）。"""
        from ..ai.client import DeepSeekClient
        from ..ai.long_term_context import load_long_term_context
        from ..ai.planner import AIPlanner
        from ..database.connection import get_connection
        from ..database.repository import TaskRepository
        from ..database.study_plan_repository import StudyPlanRepository
        from ..services.daily_planner_service import DailyPlannerService
        from ..services.date_service import DateService
        from ..services.study_plan_service import StudyPlanService
        from ..services.task_review_service import TaskReviewService
        from ..services.task_service import TaskService

        app = QApplication(sys.argv)
        conn = get_connection()
        repo = TaskRepository(conn)
        task_service = TaskService(repo)
        study_plan_service = StudyPlanService(repo, StudyPlanRepository(conn))
        study_plan_service.ensure_default_plan()
        ai_client = DeepSeekClient()
        daily_planner = DailyPlannerService(
            repo,
            StudyPlanRepository(conn),
            planner=AIPlanner(
                ai_client,
                long_term_context=load_long_term_context(),
            ),
            study_plan_service=study_plan_service,
        )
        window = MainWindow(
            task_service,
            DateService(
                repo,
                study_plan_service=study_plan_service,
                daily_planner_service=daily_planner,
            ),
            review_service=TaskReviewService(ai_client),
            study_plan_service=study_plan_service,
            daily_planner_service=daily_planner,
        )
        return window.run_app()
