"""主窗口。

职责：
- 组装 UI（标题栏、今日任务列表、统计栏）；
- 作为 UI 层唯一与 service 交互的入口，将 TaskWidget 的操作信号
  转成对 TaskService / DateService 的调用；
- 启动时执行日期切换检查；
- 系统托盘：点 X 最小化到托盘（进程继续运行），托盘菜单提供“打开/退出”；
  平台不支持托盘时自动降级，不影响运行。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QIcon,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
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

from ..services.date_service import DateService
from ..services.manual_task_service import ManualTaskService
from ..services.task_review_service import TaskReviewService
from ..services.task_service import TaskService
from ..utils.date_utils import add_days, today as _default_today
from ..database.schema import STATUS_CANCELLED
from .ai_worker import (
    AIReviewWorker,
    AIRouteBuilderWorker,
    AssessmentWorker,
    RouteSuggestionWorker,
    run_start_assessment,
)
from .assessment_dialog import AssessmentDialog
from .dialogs import AIReviewDialog, NotDoneDialog
from .manual_task_dialog import KIND_TODO, AddLearningTaskDialog
from .styles import APP_STYLE, apply_secondary_button_text
from .task_widget import TaskWidget

POSTPONE_WARNING = "该任务已经连续延期 3 次，请考虑拆分任务或调整计划。"


@dataclass
class TodayViewState:
    """今日页可恢复的视图状态（第一版只保存滚动位置）。"""

    scroll_value: int = 0


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
        assessment_service=None,
        assessment_repo=None,
        review_scheduler=None,
        skill_service=None,
        jd_service=None,
        jd_summary_service=None,
        outcome_service=None,
        notes_service=None,
        assessment_service_factory=None,
        manual_task_service=None,
        route_service=None,
        route_plan_service=None,
        route_progress_service=None,
        ai_route_service=None,
        scheduler=None,
        db_path=None,
        ai_config_service=None,
        prompt_registry=None,
        prompt_preview_service=None,
        topic_learning_service=None,
        capability_service=None,
        practice_service=None,
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
        # Phase 3D~6 服务：可选；未传则对应区域隐藏
        self.assessment_service = assessment_service
        self.assessment_repo = assessment_repo
        # 验收后台 worker 的线程安全依赖：只传 db_path + 工厂，不跨线程传连接。
        # 未显式提供工厂时回退到主线程 service（仅用于单线程测试）。
        self.db_path = db_path
        self.assessment_service_factory = (
            assessment_service_factory
            if assessment_service_factory is not None
            else (lambda conn: self.assessment_service)
        )
        # 复习调度服务（ReviewService，区别于上面的 review_service=TaskReviewService）
        self.review_scheduler = review_scheduler
        # Phase A~E 服务：可选；未传则对应职业面板隐藏（不回归旧行为）
        self.skill_service = skill_service
        self.jd_service = jd_service
        # 每日 JD 技术汇总服务（Step 5）：只读展示 + 保存，不接 Planner
        self.jd_summary_service = jd_summary_service
        self.outcome_service = outcome_service
        self.notes_service = notes_service
        # Phase C：学习路线服务（可选；不传则隐藏“学习路线”页）
        self.route_service = route_service
        self.route_plan_service = route_plan_service
        # Phase E：路线进度/掌握/复习状态
        self.route_progress_service = route_progress_service
        # Phase F：AI 路线草稿（纯 AI，不碰 DB，可跨线程）
        self.ai_route_service = ai_route_service
        # Phase D：多路线全局调度（可选；未传则回退单路线 Planner）
        self.scheduler = scheduler
        # AI 设置中心：可选；未传则隐藏“AI 设置”页
        self.ai_config_service = ai_config_service
        self.prompt_registry = prompt_registry
        self.prompt_preview_service = prompt_preview_service
        # Phase 2：Topic Learning Activity（可选）
        self.topic_learning_service = topic_learning_service
        # Phase 3：Capability Evidence（可选）
        self.capability_service = capability_service
        # Phase 4：Practice / Project Layer（可选）
        self.practice_service = practice_service
        # Phase A：手动添加今日学习任务（普通 To-do / 正式知识任务）
        self.manual_task_service = manual_task_service or ManualTaskService(
            task_service.repo,
            assessment_repo=assessment_repo,
            study_plan_service=study_plan_service,
        )

        self._task_widgets: list[TaskWidget] = []
        self._quit_requested = False
        self._tray: QSystemTrayIcon | None = None
        self._ai_workers: list[AIReviewWorker] = []
        # 防止连续双击【开始验收】创建多个 worker / 多个 pending attempt
        self._assessment_inflight: set[int] = set()

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

        # 顶部导航 [今日] [学习路线] [月总结] [AI 设置]
        nav = QHBoxLayout()
        self.nav_today_btn = QPushButton("今日")
        self.nav_routes_btn = QPushButton("学习路线")
        self.nav_monthly_btn = QPushButton("月总结")
        self.nav_practice_btn = QPushButton("实践项目")
        self.nav_ai_btn = QPushButton("AI 设置")
        self.nav_today_btn.clicked.connect(lambda: self._switch_page(0))
        self.nav_routes_btn.clicked.connect(self._switch_to_routes)
        self.nav_practice_btn.clicked.connect(self._switch_to_practice)
        self.nav_monthly_btn.clicked.connect(lambda: self._switch_page(1))
        self.nav_ai_btn.clicked.connect(self._switch_to_ai_settings)
        for b in (self.nav_today_btn, self.nav_routes_btn, self.nav_practice_btn,
                  self.nav_monthly_btn, self.nav_ai_btn):
            b.setObjectName("PrimaryButton")
            nav.addWidget(b)
        nav.addStretch()
        root.addLayout(nav)
        self.nav_layout = nav

        # 页面栈：0=今日 1=月总结
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

        # 手动添加今日学习任务（不依赖 Agent 规划）+ 路线筛选
        add_row = QHBoxLayout()
        add_row.addWidget(QLabel("路线筛选"))
        self.route_filter_combo = QComboBox()
        self.route_filter_combo.currentIndexChanged.connect(
            self._on_route_filter_changed
        )
        add_row.addWidget(self.route_filter_combo)
        add_row.addStretch()
        self.add_task_btn = QPushButton("＋ 添加学习任务")
        self.add_task_btn.setObjectName("SecondaryButton")
        apply_secondary_button_text(self.add_task_btn)
        self.add_task_btn.clicked.connect(self._on_add_learning_task)
        add_row.addWidget(self.add_task_btn)
        root_today.addLayout(add_row)

        self.route_stats_label = QLabel("")
        self.route_stats_label.setObjectName("TaskMeta")
        root_today.addWidget(self.route_stats_label)

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

        self.stack.addWidget(today_page)
        self.monthly_page_index = None
        self.routes_page_index = None
        self.ai_settings_page_index = None

        # ----- 月总结页（可选，索引 1） -----
        if self.summary_service is not None:
            from .summary_pages import MonthlySummaryPage

            self.monthly_page = MonthlySummaryPage(
                self.summary_service, today_provider=self.today_provider
            )
            self.stack.addWidget(self.monthly_page)
            self.monthly_page_index = self.stack.count() - 1
            self.nav_monthly_btn.setEnabled(True)
        else:
            self.nav_monthly_btn.setEnabled(False)

        # ----- 学习路线页（可选） -----
        if self.route_service is not None:
            from .routes_page import LearningRoutesPage

            self.routes_page = LearningRoutesPage(
                self.route_service, route_plan_service=self.route_plan_service,
                progress_service=self.route_progress_service,
                today_provider=self.today_provider,
                ai_route_service=self.ai_route_service,
                skill_service=self.skill_service,
                topic_learning_service=self.topic_learning_service,
                capability_service=self.capability_service,
                outcome_service=self.outcome_service,
                practice_service=self.practice_service,
            )
            self.stack.addWidget(self.routes_page)
            self.routes_page_index = self.stack.count() - 1
            self.nav_routes_btn.setEnabled(True)
        else:
            self.nav_routes_btn.setEnabled(False)

        # ----- 实践项目页（可选，独立一级页面） -----
        self.practice_page_index = None
        if self.practice_service is not None:
            from .practice_page import PracticeProjectsPage

            self.practice_page = PracticeProjectsPage(
                self.practice_service,
                getattr(self.route_service, "route_repo", None),
                self.skill_service.skill_repo if self.skill_service else None,
                self.practice_service.plan_repo,
            )
            self.stack.addWidget(self.practice_page)
            self.practice_page_index = self.stack.count() - 1
            self.nav_practice_btn.setEnabled(True)
        else:
            self.nav_practice_btn.setEnabled(False)

        # ----- AI 设置页（可选） -----
        if self.ai_config_service is not None and self.prompt_registry is not None:
            from .ai_settings_page import AISettingsPage

            self.ai_settings_page = AISettingsPage(
                self.ai_config_service,
                self.prompt_registry,
                preview_service=self.prompt_preview_service,
            )
            self.stack.addWidget(self.ai_settings_page)
            self.ai_settings_page_index = self.stack.count() - 1
            self.nav_ai_btn.setEnabled(True)
        else:
            self.nav_ai_btn.setEnabled(False)

        self.setCentralWidget(central)
        self.statusBar().showMessage("")

    def _switch_page(self, index: int) -> None:
        """切换今日 / 月总结页面（保留旧索引语义）。"""
        if index == 0:
            self.stack.setCurrentIndex(0)
            return
        if index == 1 and self.monthly_page_index is not None:
            self.stack.setCurrentIndex(1)
            return
        if index == 1:
            self.statusBar().showMessage("月总结不可用", 3000)
            return
        self.stack.setCurrentIndex(index)

    def _switch_to_routes(self) -> None:
        if self.routes_page_index is None:
            self.statusBar().showMessage("学习路线不可用", 3000)
            return
        if self.routes_page is not None:
            self.routes_page.refresh()
        self.stack.setCurrentIndex(self.routes_page_index)

    def _switch_to_ai_settings(self) -> None:
        if self.ai_settings_page_index is None:
            self.statusBar().showMessage("AI 设置不可用", 3000)
            return
        if getattr(self, "ai_settings_page", None) is not None:
            self.ai_settings_page.refresh()
        self.stack.setCurrentIndex(self.ai_settings_page_index)

    def _switch_to_practice(self) -> None:
        if getattr(self, "practice_page_index", None) is None:
            self.statusBar().showMessage("实践项目不可用", 3000)
            return
        if getattr(self, "practice_page", None) is not None:
            self.practice_page.refresh()
        self.stack.setCurrentIndex(self.practice_page_index)

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
        """启动流程：日期切换 -> 生成今日到期复习 -> 加载今日任务。"""
        today_str = self.today_provider()
        self.date_service.process_date_transition(today_str)
        # 复习调度（幂等）：为今天到期的知识点生成复习任务
        if self.review_scheduler is not None:
            try:
                self.review_scheduler.generate_due_reviews(today=today_str)
            except Exception:  # noqa: BLE001 - 复习生成失败不影响启动
                pass
            # 到期不足时补足“每日巩固”（幂等；总量目标默认 3）
            try:
                self.review_scheduler.generate_daily_retention_reviews(
                    today=today_str
                )
            except Exception:  # noqa: BLE001 - 巩固生成失败不影响启动
                pass
        self.current_date = today_str
        self.date_label.setText(today_str)
        self.refresh()

    def refresh(self, preserve_scroll: bool = False) -> None:
        """重建今日页（新知识 / 复习 + 职业面板）。

        :param preserve_scroll: 同页面 mutation（完成/未完成/移除/复习完成等）
            时置 True，重建后恢复原滚动位置，避免自动跳到底部。
        """
        state = self.capture_today_view_state() if preserve_scroll else None
        today_str = self.current_date
        tasks = self.task_service.get_tasks_by_date(today_str)
        self._today_tasks = tasks

        self._update_phase_info(today_str)
        self._update_planner_info()

        # 路线筛选选项 + 当前选择
        self._reload_route_filter()
        selected_route = self._selected_route_filter()
        self._route_names = self._route_name_map()

        # 清空滚动区动态内容
        self._clear_dynamic_list()
        self._task_widgets.clear()
        self._career_panel_added = False

        # legacy 兼容：
        # - review 单独区域；
        # - 已移除的 extra 不再展示（历史记录保留在 DB，但不作为产品功能）。
        new_tasks = [
            t for t in tasks
            if t.task_type not in ("review", "extra")
            and t.status != STATUS_CANCELLED
            and self._matches_route(t, selected_route)
        ]
        review_tasks = [
            t for t in tasks
            if t.task_type == "review" and t.status != STATUS_CANCELLED
            and self._matches_route(t, selected_route)
        ]
        # 用户主动移除的任务：不进入“今日待执行任务”，仅折叠提示
        cancelled_tasks = [
            t for t in tasks
            if t.status == STATUS_CANCELLED
            and self._matches_route(t, selected_route)
        ]
        self._update_route_stats(tasks, selected_route)

        # 1) 今日新知识
        self._add_section_header("今日新知识")
        for t in new_tasks:
            self._add_task_widget(t)
        if cancelled_tasks:
            names = "、".join(t.title for t in cancelled_tasks)
            self._add_section_hint(
                f"已移除今日任务 {len(cancelled_tasks)} 个（不计入完成率）：{names}"
            )

        # 2) 今日复习
        if self.review_scheduler is not None or review_tasks:
            self._add_section_header("今日复习")
            if review_tasks:
                for t in review_tasks:
                    self._add_task_widget(t)
            else:
                self._add_section_hint("暂无可复习内容")

        # Phase E：职业 / 技能 / JD 面板（可选注入，异常不崩溃）
        self._add_skill_overview()
        self._add_jd_trend_panel()

        self.list_layout.addStretch()

        has_effective = any(t.status != STATUS_CANCELLED for t in tasks)
        has_content = bool(self._task_widgets) or self._career_panel_added
        scroll_visible = has_effective or self._career_panel_added
        self.empty_hint.setVisible(not has_content)
        self.scroll.setVisible(scroll_visible)

        if state is not None:
            self.restore_today_view_state(state)

    # ---------- 今日页滚动位置保持 ----------

    def capture_today_view_state(self) -> TodayViewState:
        try:
            bar = self.scroll.verticalScrollBar()
            return TodayViewState(scroll_value=int(bar.value()))
        except Exception:  # noqa: BLE001
            return TodayViewState()

    def restore_today_view_state(self, state: TodayViewState) -> None:
        """在 layout 完成后恢复滚动位置（clamp 到当前 maximum）。

        Qt 的 deleteLater / layout 是异步的，且被删除的焦点控件会让 Qt 自动
        ensureWidgetVisible 而滚动；因此先清除焦点，再用 QTimer 在事件循环后
        恢复，并做一次延迟兜底。
        """
        from PySide6.QtWidgets import QApplication

        focused = QApplication.focusWidget()
        if focused is not None:
            focused.clearFocus()

        def _apply() -> None:
            try:
                bar = self.scroll.verticalScrollBar()
                if bar.maximum() <= 0 and state.scroll_value > 0:
                    return  # layout 还未完成，等待下一个 timer
                bar.setValue(min(int(state.scroll_value), bar.maximum()))
            except Exception:  # noqa: BLE001
                pass

        _apply()
        for delay in (0, 16, 60, 160):
            QTimer.singleShot(delay, _apply)

    # ---------- Phase C：路线筛选 / 统计 ----------

    def _route_name_map(self) -> dict:
        if self.route_service is None:
            return {}
        try:
            return {
                r.id: r.name
                for r in self.route_service.route_repo.list_all()
            }
        except Exception:  # noqa: BLE001
            return {}

    def _active_learning_routes(self) -> list:
        if self.route_service is None:
            return []
        try:
            return [
                r for r in self.route_service.route_repo.list_learning_routes()
                if not r.is_archived
            ]
        except Exception:  # noqa: BLE001
            return []

    def _reload_route_filter(self) -> None:
        """重建“全部路线 / 各 learning route / 未分类”筛选项（保留当前选择）。"""
        self._route_filter_loading = True
        combo = self.route_filter_combo
        previous = combo.currentData() if combo.count() else "all"
        combo.clear()
        combo.addItem("全部路线", "all")
        for r in self._active_learning_routes():
            combo.addItem(r.name, r.id)
        combo.addItem("未分类", "none")
        idx = combo.findData(previous)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._route_filter_loading = False

    def _selected_route_filter(self):
        if not hasattr(self, "route_filter_combo") or \
                self.route_filter_combo.count() == 0:
            return "all"
        return self.route_filter_combo.currentData()

    @staticmethod
    def _matches_route(task, selected) -> bool:
        if selected in (None, "all"):
            return True
        if selected == "none":
            return task.route_id is None
        return task.route_id == selected

    def _on_route_filter_changed(self) -> None:
        if getattr(self, "_route_filter_loading", False):
            return
        self.refresh()

    def _update_route_stats(self, tasks, selected) -> None:
        if not hasattr(self, "route_stats_label"):
            return
        # 完成率分母 = 过滤后 status != cancelled 的任务；cancelled 永不进分母
        effective = [
            t for t in tasks
            if t.status != STATUS_CANCELLED
            and self._matches_route(t, selected)
        ]
        done = sum(1 for t in effective if t.status == "done")
        if selected in (None, "all"):
            label = "全部路线"
        elif selected == "none":
            label = "未分类"
        else:
            label = (self._route_names or {}).get(selected, f"路线{selected}")
        self.route_stats_label.setText(
            f"{label}：完成 {done} / {len(effective)}"
        )

    # ---------- 今日页区域构建 ----------

    def _clear_dynamic_list(self) -> None:
        """清空滚动区内所有动态加入的项（含伸展符）。"""
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _add_section_header(self, title: str) -> None:
        lbl = QLabel(title)
        lbl.setObjectName("SectionTitle")
        self.list_layout.addWidget(lbl)

    def _add_section_hint(self, text: str) -> None:
        hint = QLabel(text)
        hint.setObjectName("EmptyHint")
        self.list_layout.addWidget(hint)

    def _add_task_widget(self, task) -> None:
        label = "开始验收"
        if self.assessment_repo is not None and (
            task.knowledge_point_id is not None or task.topic_id is not None
        ):
            try:
                if self.assessment_repo.find_pending_attempt_for_task(task.id):
                    label = "继续验收"
            except Exception:  # noqa: BLE001 - 仅影响按钮文案
                pass
        widget = TaskWidget(
            task,
            assessment_label=label,
            route_name=(self._route_names or {}).get(task.route_id),
        )
        widget.complete_requested.connect(self._on_complete)
        widget.not_done_requested.connect(self._on_not_done)
        widget.postpone_requested.connect(self._on_postpone)
        widget.remove_requested.connect(self._on_remove_task)
        widget.assessment_requested.connect(self._on_start_assessment)
        self.list_layout.addWidget(widget)
        self._task_widgets.append(widget)

    # ---------- Phase A：手动添加 / 移除今日任务 ----------

    def _available_topics(self) -> list[dict]:
        """当前计划下的全部 study_topics（供“关联已有 Topic”下拉）。"""
        if self.study_plan_service is None:
            return []
        try:
            plan = self.study_plan_service.get_active_plan_full()
        except Exception:  # noqa: BLE001 - 计划异常不影响手动加任务
            return []
        if plan is None:
            return []
        out: list[dict] = []
        for phase in plan.phases:
            for topic in phase.topics:
                out.append({"id": topic.id, "name": topic.name})
        return out

    def _topics_by_route(self) -> dict:
        """{route_id: [topic,...]}，供 AddLearningTaskDialog 按路线过滤 topic。"""
        if self.study_plan_service is None:
            return {}
        out: dict = {}
        for r in self._active_learning_routes():
            try:
                topics = self.study_plan_service.plan_repo.list_topics_by_route(r.id)
            except Exception:  # noqa: BLE001
                topics = []
            out[r.id] = [{"id": t.id, "name": t.name} for t in topics]
        return out

    def _on_add_learning_task(self) -> None:
        """打开添加学习任务对话框（普通 To-do / 正式知识学习任务）。"""
        from .dialogs import show_warning

        routes = [
            {"id": r.id, "name": r.name} for r in self._active_learning_routes()
        ]
        dlg = AddLearningTaskDialog(
            topics=self._available_topics(),
            default_date=self.current_date,
            parent=self,
            routes=routes,
            topics_by_route=self._topics_by_route(),
            topic_learning_service=self.topic_learning_service,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dlg.result_payload()
        try:
            if payload["kind"] == KIND_TODO:
                self.manual_task_service.create_todo(
                    title=payload["title"],
                    description=payload["description"],
                    estimated_minutes=payload["estimated_minutes"],
                    scheduled_date=payload["scheduled_date"],
                    route_id=payload.get("route_id"),
                )
                msg = "已添加普通学习任务"
            else:
                self.manual_task_service.create_knowledge_task(
                    title=payload["title"],
                    description=payload["description"],
                    estimated_minutes=payload["estimated_minutes"],
                    scheduled_date=payload["scheduled_date"],
                    topic_id=payload.get("topic_id"),
                    route_id=payload.get("route_id"),
                    component_id=payload.get("component_id"),
                    learning_activity_kind=payload.get("learning_activity_kind"),
                )
                msg = "已添加正式知识学习任务"
        except Exception as e:  # noqa: BLE001 - 添加失败不崩溃
            show_warning(self, f"添加任务失败：{e}")
            return
        self.refresh(preserve_scroll=True)
        self.statusBar().showMessage(msg, 4000)

    def _confirm_remove_dialog(self) -> bool:
        """移除确认框：默认“取消”，明确告知不会删除学习内容。"""
        box = QMessageBox(self)
        box.setWindowTitle("移除今日任务")
        box.setText(
            "仅从今天的学习计划中移除此任务，不会删除学习内容，"
            "以后 Agent 仍可能再次安排。"
        )
        confirm_btn = box.addButton("确认移除", QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        return box.clickedButton() is confirm_btn

    def _on_remove_task(self, task_id: int) -> None:
        """移除今日任务：active -> cancelled，绝不物理删除。"""
        from .dialogs import show_warning

        try:
            task = self.task_service.get_task(task_id)
        except Exception:  # noqa: BLE001 - 任务不存在则不处理
            return
        if not self.task_service.is_cancellable(task):
            show_warning(self, "该任务当前状态不能移除。")
            return
        # 正在 pending Assessment 的任务不允许移除
        if task.knowledge_point_id is not None and self.assessment_repo is not None:
            try:
                if self.assessment_repo.find_pending_attempt_for_task(task.id):
                    show_warning(
                        self, "该任务正在进行验收，不能移除。请先完成或取消本次验收。"
                    )
                    return
            except Exception:  # noqa: BLE001 - 校验异常时按可移除处理
                pass
        if not self._confirm_remove_dialog():
            return
        try:
            self.task_service.cancel_task(task_id)
        except Exception as e:  # noqa: BLE001
            show_warning(self, f"移除失败：{e}")
            return
        self.refresh(preserve_scroll=True)
        self.statusBar().showMessage("已移除今日任务（不算未完成）", 4000)

    # ---------- Phase E：职业面板 ----------

    def _add_label(self, text: str, object_name: str = "TaskMeta",
                   word_wrap: bool = True) -> None:
        """往滚动区加一行文本。"""
        lbl = QLabel(text)
        lbl.setObjectName(object_name)
        lbl.setWordWrap(word_wrap)
        self.list_layout.addWidget(lbl)

    # ---------- 技能概览（Phase E 简化版） ----------

    # 内部状态 -> 面向用户的文案（不暴露 learning / not_started 等）
    _STATUS_TEXT = {
        "learning": "学习中",
        "not_started": "待学习",
        "mastered": "已掌握",
        "deferred": "暂缓",
    }
    _TIER_RANK = {"S": 4, "A": 3, "B": 2, "C": 1}
    _MAX_CURRENT = 5
    _MAX_BLOCKED = 5
    _MAX_MASTERED_NAMES = 5

    def _skill_mastery_text(self, skill: dict) -> str | None:
        """只有存在真实验收证据时才返回“AI验收 NN%”，否则 None。

        只读现有 assessment evidence（knowledge_points.mastery_estimate +
        last_assessed_at），不改变任何 mastery 计算。
        """
        ref = (skill.get("mastery_ref") or "").strip()
        repo = self.assessment_repo or getattr(
            self.skill_service, "assessment_repo", None
        )
        if not ref.startswith("kp:") or repo is None:
            return None
        try:
            kp = repo.get_knowledge_point(int(ref[3:]))
        except (TypeError, ValueError):
            return None
        if not kp or not kp.get("last_assessed_at") \
                or kp.get("mastery_estimate") is None:
            return None
        pct = round(float(kp["mastery_estimate"]) * 100)
        return f"AI验收 {pct}%"

    def _skill_row(self, skill: dict, status_text: str) -> str:
        """一行技能：名称 + tier + 面向用户状态 +（可选）掌握度。"""
        line = (
            f"{skill.get('name')}    {skill.get('tier') or '?'}级 · "
            f"{status_text}"
        )
        m = self._skill_mastery_text(skill)
        if m:
            line += f" · {m}"
        return line

    def _add_skill_overview(self) -> None:
        """技能概览：当前学习 / 待解锁 / 已掌握（替代旧的技能状态面板）。"""
        if self.skill_service is None:
            return
        self._career_panel_added = True
        try:
            all_skills = self.skill_service.skill_repo.list_all()
        except Exception:  # noqa: BLE001
            self._add_section_header("技能概览")
            self._add_label("技能服务异常", object_name="QErrorMessage")
            return

        self._add_section_header("技能概览")
        if not all_skills:
            self._add_label("暂无技能数据", object_name="EmptyHint")
            return

        def _by_score(items):            return sorted(
                items,
                key=lambda s: (-(s.get("priority_score") or 0.0), s["name"]),
            )

        # ---- 当前学习：仅 (status==learning) 或 (当前 phase active topic 关联) ----
        # 注意：不能仅因为 priority_score 高 / gate 已放行(select_active_candidates)
        # 就把“未来阶段”的技能塞进“当前学习”。
        current: list[dict] = []
        seen: set[str] = set()

        def _push(skill):
            if not skill or skill["name"] in seen:
                return
            # 当前学习只包含可学习的技能（排除已掌握 / 前置未满足）
            try:
                if self.skill_service.effective_status(skill) not in (
                    "learning", "not_started"
                ):
                    return
            except Exception:  # noqa: BLE001
                pass
            seen.add(skill["name"])
            current.append(skill)

        # 1) 真正在学习的技能
        for s in _by_score(all_skills):
            if s.get("status") == "learning":
                _push(s)
        # 2) 与当前 phase 的 active study_topic 明确关联的技能
        if self.study_plan_service is not None:
            try:
                phase = self.study_plan_service.get_current_phase(self.current_date)
            except Exception:  # noqa: BLE001
                phase = None
            if phase is not None:
                names: set[str] = set()
                for topic in phase.topics:
                    try:
                        names.update(self.skill_service.skills_for_topic(topic.id) or [])
                    except Exception:  # noqa: BLE001
                        pass
                for s in _by_score(all_skills):
                    if s["name"] in names:
                        _push(s)
        current = current[: self._MAX_CURRENT]

        # ---- 待解锁：前置未满足的技能（S/A 优先，再按 priority） ----
        blocked: list[tuple[dict, list[str]]] = []
        for s in all_skills:
            if s["name"] in seen:
                continue
            try:
                if not self.skill_service.is_blocked(s):
                    continue
                missing = self.skill_service.missing_prerequisites(s)
            except Exception:  # noqa: BLE001
                continue
            blocked.append((s, missing))
        blocked.sort(key=lambda p: (
            -self._TIER_RANK.get(p[0].get("tier"), 0),
            -(p[0].get("priority_score") or 0.0),
            p[0]["name"],
        ))
        blocked = blocked[: self._MAX_BLOCKED]

        # ---- 已掌握：摘要 ----
        mastered = [s["name"] for s in all_skills if s.get("status") == "mastered"]

        # 只在确实展示了掌握度时，才写一次来源说明
        if any(self._skill_mastery_text(s) for s in current):
            self._add_label(
                "掌握度来自客观验收的 AI 估计。", object_name="TaskMeta"
            )

        # 1) 当前学习
        self._add_label("当前学习", object_name="TaskTitle")
        if current:
            for s in current:
                self._add_label(
                    self._skill_row(
                        s, self._STATUS_TEXT.get(s.get("status"), "待学习")
                    )
                )
        else:
            self._add_label("当前暂无正在学习的技能", object_name="EmptyHint")

        # 2) 待解锁
        self._add_label("待解锁", object_name="TaskTitle")
        if blocked:
            for s, missing in blocked:
                miss = "、".join(missing) if missing else "-"
                self._add_label(f"{s.get('name')}    缺：{miss}")
        else:
            self._add_label("当前无前置阻塞", object_name="EmptyHint")

        # 3) 已掌握（摘要，不逐条展开）
        self._add_label("已掌握", object_name="TaskTitle")
        if mastered:
            head = mastered[: self._MAX_MASTERED_NAMES]
            names = " / ".join(head)
            if len(mastered) > self._MAX_MASTERED_NAMES:
                names += " / …"
            self._add_label(f"已掌握 {len(mastered)} 项：{names}")
        else:
            self._add_label("暂无已掌握技能记录", object_name="EmptyHint")

    def _add_jd_trend_panel(self) -> None:
        """近期 JD 技术趋势（统一近30天）：只展示 Service 结果，不算频率、不接 Planner。

        展示：已匹配技能频率 + JD 新技能候选（可加入/忽略） + 课程缺口。
        """
        if self.jd_summary_service is None and self.jd_service is None:
            return
        self._career_panel_added = True
        self._add_section_header("近期 JD 技术趋势")

        trend = None
        error = None
        candidates: list[dict] = []
        if self.jd_summary_service is not None:
            try:
                trend = self.jd_summary_service.compute_skill_trends(
                    self.current_date, 30, "internship"
                )
                candidates = self.jd_summary_service.refresh_candidates(
                    self.current_date, 30, "internship"
                )
            except Exception:  # noqa: BLE001 - 趋势异常不崩溃
                error = "JD 趋势服务异常"

        if error is not None:
            self._add_label(error, object_name="QErrorMessage")
        elif trend is None or (
            not trend["skills"] and not candidates
        ):
            self._add_label("暂无近期 JD 技术汇总", object_name="EmptyHint")
        else:
            self._add_label(f"近30天样本：{trend['sample_count']} 个实习岗位")
            self._add_label(
                "以下趋势基于你最近收集的目标岗位样本。",
                object_name="TaskMeta",
            )
            for r in trend["skills"][:8]:
                self._add_label(
                    f"{r['name']}    {r['frequency'] * 100:.1f}%"
                )
            self._add_jd_candidates(candidates)
            self._add_skill_gap_from_trend(trend)
            self._add_curriculum_gap()
            self._add_label(
                "频率 = 近30天汇总中提到该技能的岗位数 / 总样本岗位数。",
                object_name="TaskMeta",
            )

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        if self.jd_summary_service is not None:
            add_btn = QPushButton("添加今日 JD 技术汇总")
            add_btn.setObjectName("SecondaryButton")
            apply_secondary_button_text(add_btn)
            add_btn.clicked.connect(self._on_add_jd_summary)
            btn_row.addWidget(add_btn)
        if self.jd_service is not None:
            hist_btn = QPushButton("查看历史 JD")
            hist_btn.setObjectName("SecondaryButton")
            apply_secondary_button_text(hist_btn)
            hist_btn.clicked.connect(self._on_view_history_jd)
            btn_row.addWidget(hist_btn)
        btn_row.addStretch()
        btn_w = QWidget()
        btn_w.setLayout(btn_row)
        self.list_layout.addWidget(btn_w)

    def _add_jd_candidates(self, candidates: list[dict]) -> None:
        """JD 新技能候选：高频但当前无等价正式技能，用户可加入/忽略。"""
        if not candidates:
            return
        self._add_label("JD 新技能候选", object_name="TaskTitle")
        self._add_label(
            "这些技术在近30天 JD 中高频出现，但尚无等价正式技能；"
            "确认后才会加入技能体系。",
            object_name="TaskMeta",
        )
        for c in candidates[:6]:
            row_w = QWidget()
            row = QHBoxLayout(row_w)
            row.setContentsMargins(0, 0, 0, 0)
            info = QLabel(
                f"{c['canonical_name']}    {c['mention_count_30d']} 次 · "
                f"近30天 {c['frequency_30d'] * 100:.0f}%"
            )
            info.setObjectName("TaskMeta")
            add_btn = QPushButton("加入技能体系")
            add_btn.setObjectName("SecondaryButton")
            apply_secondary_button_text(add_btn)
            add_btn.clicked.connect(
                lambda _=False, cid=c["id"]: self._on_accept_candidate(cid)
            )
            ign_btn = QPushButton("忽略")
            ign_btn.setObjectName("SecondaryButton")
            apply_secondary_button_text(ign_btn)
            ign_btn.clicked.connect(
                lambda _=False, cid=c["id"]: self._on_ignore_candidate(cid)
            )
            row.addWidget(info, 1)
            row.addWidget(add_btn)
            row.addWidget(ign_btn)
            self.list_layout.addWidget(row_w)

    def _add_curriculum_gap(self) -> None:
        """课程缺口：正式技能近30天高频但当前无正式学习主题。只读展示。"""
        if self.skill_service is None:
            return
        try:
            gaps = self.skill_service.curriculum_gap_skills()
        except Exception:  # noqa: BLE001
            return
        if not gaps:
            return
        self._add_label("课程缺口", object_name="TaskTitle")
        for g in gaps[:6]:
            self._add_label(
                f"{g['skill']}    近30天需求 {g['frequency_30d'] * 100:.0f}%"
                "    暂无正式学习主题",
                object_name="TaskMeta",
            )

    def _add_skill_gap_from_trend(self, trend: dict) -> None:
        """只读展示“高频但未掌握 / 前置未满足”，gate 全部来自 SkillService。"""
        if self.skill_service is None:
            return
        try:
            by_name = {s["name"]: s for s in self.skill_service.skill_repo.list_all()}
        except Exception:  # noqa: BLE001
            return
        gaps: list[str] = []
        blocked: list[tuple[str, list[str]]] = []
        for r in trend.get("skills") or []:
            skill = by_name.get(r["name"])
            if skill is None:
                continue
            try:
                if self.skill_service.is_blocked(skill):
                    blocked.append((
                        r["name"],
                        self.skill_service.missing_prerequisites(skill),
                    ))
                elif skill.get("status") != "mastered":
                    gaps.append(r["name"])
            except Exception:  # noqa: BLE001
                continue
        if gaps:
            self._add_label("当前主要技能缺口", object_name="TaskTitle")
            for name in gaps[:5]:
                self._add_label(f"{name}    高频 · 尚未掌握")
        if blocked:
            self._add_label("暂不提前", object_name="TaskTitle")
            for name, missing in blocked[:5]:
                self._add_label(f"{name}    缺：{'、'.join(missing)}")

    def _on_add_jd_summary(self) -> None:
        """添加今日 JD 技术汇总：只保存 + 刷新 UI；不重规划、不改 active task。"""
        if self.jd_summary_service is None:
            return
        from .career_dialogs import JdSummaryInputDialog

        dlg = JdSummaryInputDialog(
            self.jd_summary_service, self.current_date, parent=self
        )
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.saved:
            saved = dlg.saved
            # Step 6：市场信号变化 → 重算技能优先级（不重写当前 active task）
            if self.skill_service is not None:
                try:
                    market = self.skill_service.refresh_market(
                        saved["summary_date"]
                    )
                    self.skill_service.recompute_all_priority_scores(
                        saved["summary_date"]
                    )
                    if not market or market.get("source") != "daily_summary":
                        pass
                except Exception:  # noqa: BLE001 - 重算失败不影响保存
                    pass
            self.statusBar().showMessage(
                f"{saved['summary_date']} JD 技术汇总已保存，共 "
                f"{saved['sample_count']} 个岗位样本。"
                "近期岗位需求已更新，将影响后续学习规划。",
                6000,
            )
            self.refresh(preserve_scroll=True)

    def _on_view_history_jd(self) -> None:
        if self.jd_service is None:
            return
        from .career_dialogs import JdHistoryDialog

        JdHistoryDialog(self.jd_service, parent=self).exec()

    def _on_accept_candidate(self, candidate_id: int) -> None:
        """用户确认把 JD 新技能候选加入正式技能体系（不自作主张）。"""
        if self.jd_summary_service is None:
            return
        cand = None
        try:
            for c in self.jd_summary_service.list_candidates():
                if c["id"] == candidate_id:
                    cand = c
                    break
        except Exception:  # noqa: BLE001
            cand = None
        if cand is None:
            return
        from .career_dialogs import JdCandidateAcceptDialog

        existing_names = []
        if self.skill_service is not None:
            try:
                existing_names = [
                    s["name"] for s in self.skill_service.skill_repo.list_all()
                ]
            except Exception:  # noqa: BLE001
                existing_names = []
        routes = []
        if self.route_service is not None:
            try:
                routes = [
                    {"id": r.id, "name": r.name, "goal": r.goal or ""}
                    for r in self.route_service.route_repo.list_learning_routes()
                    if not r.is_archived
                ]
            except Exception:  # noqa: BLE001
                routes = []
        # 确定性优先：若同名技能已存在，直接显示已有关联
        existing_route_ids: list[int] = []
        suggested_name = (cand.get("suggested_name")
                          or cand.get("canonical_name") or "").strip()
        if suggested_name and self.skill_service is not None:
            try:
                s = self.skill_service.skill_repo.get_by_name(suggested_name)
                if s is not None and self.route_service is not None:
                    existing_route_ids = self.route_service.route_repo \
                        .list_route_ids_for_skill(s["id"])
            except Exception:  # noqa: BLE001
                existing_route_ids = []
        dlg = JdCandidateAcceptDialog(
            cand, existing_names=existing_names, parent=self,
            routes=routes, existing_route_ids=existing_route_ids,
        )
        # AI 建议：仅在无确定关联且 AI 可用时；失败不影响手动选择
        self._suggestion_worker = None
        if (not existing_route_ids and routes
                and self.ai_route_service is not None
                and self.ai_route_service.is_configured()):
            worker = RouteSuggestionWorker(
                self.ai_route_service, suggested_name, routes, parent=self
            )
            worker.succeeded.connect(
                lambda sugg, d=dlg: d.apply_ai_suggestion(
                    sugg.suggested_route_names, sugg.reason
                )
            )
            worker.failed.connect(
                lambda _msg, d=dlg: d.set_suggestion_failed()
            )
            self._suggestion_worker = worker
            worker.start()
        elif routes:
            dlg.set_suggestion_failed()
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            result = self.jd_summary_service.accept_candidate(
                candidate_id,
                name=dlg.result_name,
                tier=dlg.result_tier,
                linked_skill=dlg.result_linked_skill,
                route_ids=dlg.result_route_ids,
            )
        except Exception as e:  # noqa: BLE001 - 不崩溃
            self.statusBar().showMessage(f"加入技能失败：{e}", 5000)
            return
        skill = (result or {}).get("skill") or {}
        if self.skill_service is not None:
            try:
                self.skill_service.sync_skill_topic_links()
                self.skill_service.refresh_market(self.current_date)
                self.skill_service.recompute_all_priority_scores(
                    self.current_date
                )
            except Exception:  # noqa: BLE001
                pass
            gaps = []
            try:
                gaps = [
                    g["skill"]
                    for g in self.skill_service.curriculum_gap_skills()
                ]
            except Exception:  # noqa: BLE001
                gaps = []
        else:
            gaps = []
        name = skill.get("name", dlg.result_name)
        if name in gaps:
            self.statusBar().showMessage(
                f"已加入技能「{name}」，但暂无正式课程（已标记为课程缺口）。",
                6000,
            )
        else:
            self.statusBar().showMessage(f"已加入技能「{name}」。", 5000)
        self.refresh(preserve_scroll=True)

    def _on_ignore_candidate(self, candidate_id: int) -> None:
        if self.jd_summary_service is None:
            return
        try:
            self.jd_summary_service.ignore_candidate(candidate_id)
        except Exception:  # noqa: BLE001
            return
        self.statusBar().showMessage("已忽略该 JD 新技能候选。", 3000)
        self.refresh(preserve_scroll=True)

    def _add_jd_panel(self) -> None:
        """兼容保留：旧“最新 JD / 岗位需求”面板（已由 _add_jd_trend_panel 取代）。"""
        self._add_jd_trend_panel()

    # ---------- 职业面板处理器 ----------

    def _on_add_jd(self) -> None:
        from .career_dialogs import JdInputDialog

        dlg = JdInputDialog(self.jd_service, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.statusBar().showMessage(
                "JD 已保存，技能优先级已更新（只影响近期优先级）", 6000
            )
            if self.jd_service is not None:
                self.skill_service.recompute_all_priority_scores()
                self.refresh(preserve_scroll=True)

    def _show_jd_detail(self, jd: dict) -> None:
        from .career_dialogs import JdDetailDialog

        JdDetailDialog(self.jd_service, jd, parent=self).exec()

    # ---------- 验收流程 ----------

    def _on_start_assessment(self, task_id: int) -> None:
        """开始验收：创建/复用 pending 验收记录，并弹出验收对话框。"""
        try:
            task = self.task_service.get_task(task_id)
        except Exception:  # noqa: BLE001
            return
        svc = self.assessment_service
        if svc is None:
            self.statusBar().showMessage("验收功能不可用", 3000)
            return
        if task.knowledge_point_id is None:
            # 安全网：正常应由启动 repair / 新任务创建链路完成关联。
            # 若某任务漏了（如本次会话内新生成），只要它有 topic_id 就现场
            # 幂等补齐 topic -> knowledge_point 关联（只补关系、不补证据）；
            # 没有 topic 的任务（manual / extra 等）仍明确不支持验收。
            healed = self._ensure_task_knowledge_point(task)
            if healed is None or healed.knowledge_point_id is None:
                self.statusBar().showMessage(
                    "该任务暂不支持验收（无关联知识点）", 3000
                )
                return
            task = healed
        # Phase E：route 一致性守卫（task.route_id 与 kp.route_id 不得串路线）
        if (task.route_id is not None and task.knowledge_point_id is not None
                and self.assessment_repo is not None):
            try:
                kp = self.assessment_repo.get_knowledge_point(
                    task.knowledge_point_id
                )
            except Exception:  # noqa: BLE001
                kp = None
            if kp is not None and kp.get("route_id") is not None \
                    and kp["route_id"] != task.route_id:
                from .dialogs import show_warning

                show_warning(
                    self,
                    "该任务与知识点的所属路线不一致，已阻止跨路线验收。",
                )
                return
        if not svc.is_configured():
            from .dialogs import show_warning

            show_warning(self, "AI 未配置，无法开始验收。")
            return

        existing = None
        if self.assessment_repo is not None:
            existing = self.assessment_repo.find_pending_attempt_for_task(task.id)
        if existing is not None:
            self._open_assessment_dialog(existing)
            return

        if task.id in self._assessment_inflight:
            return  # 已有验收 worker 在跑，忽略重复点击
        self._assessment_inflight.add(task.id)
        self.statusBar().showMessage("正在生成验收题…", 0)
        worker = AssessmentWorker(
            run_start_assessment,
            self.assessment_service_factory,
            db_path=self.db_path,
            args=(task.knowledge_point_id,),
            kwargs={"task_id": task.id},
            parent=self,
        )
        worker.succeeded.connect(self._on_assessment_ready)
        worker.failed.connect(self._on_assessment_failed)
        worker.finished.connect(
            lambda w=worker, tid=task.id: self._release_assessment_worker(w, tid)
        )
        self._ai_workers.append(worker)
        worker.start()

    def _ensure_task_knowledge_point(self, task):
        """验收安全网：为缺 knowledge_point_id 的 generated/new 任务幂等补关联。

        只补关系、不补证据（不创建 assessment、不设置 mastery）。无 topic 或
        非 generated/new 的任务原样返回（knowledge_point_id 仍为 None）。
        """
        if self.study_plan_service is None:
            return task
        try:
            return self.study_plan_service.link_task_knowledge_point(task)
        except Exception:  # noqa: BLE001 - 安全网失败不应崩溃
            return task

    def _on_assessment_ready(self, attempt) -> None:
        self.statusBar().clearMessage()
        self._open_assessment_dialog(attempt)

    def _on_assessment_failed(self, msg: str) -> None:
        self.statusBar().clearMessage()
        self.statusBar().showMessage(f"验收启动失败：{msg}", 5000)

    def _open_assessment_dialog(self, attempt) -> None:
        dlg = AssessmentDialog(
            self.assessment_service, attempt, self.current_date, parent=self,
            service_factory=self.assessment_service_factory,
            db_path=self.db_path,
        )
        dlg.assessment_completed.connect(self._on_assessment_completed)
        dlg.exec()

    def _on_assessment_completed(self, task_id: int) -> None:
        """验收成功：复习任务自动标记完成并刷新。"""
        try:
            task = self.task_service.get_task(task_id)
            if task.task_type == "review":
                self.task_service.complete_task(task_id)
        except Exception:  # noqa: BLE001
            pass
        self.refresh(preserve_scroll=True)

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

    def _planning_paused(self) -> bool:
        if self.study_plan_service is None:
            return False
        try:
            return not self.study_plan_service.is_planning_enabled()
        except Exception:  # noqa: BLE001
            return False

    def _planning_route_name(self) -> str:
        if self.route_service is None:
            return "当前学习路线"
        try:
            default = self.route_service.route_repo.get_default_learning_route()
            if default is not None:
                return default.name
        except Exception:  # noqa: BLE001
            pass
        return "当前学习路线"

    def _update_planner_info(self) -> None:
        """刷新 AI 今日规划区域的可用状态。"""
        if self.scheduler is not None:
            plannable = []
            try:
                plannable = self.scheduler.plannable_routes(self.current_date)
            except Exception:  # noqa: BLE001
                plannable = []
            if plannable:
                names = "、".join(r.name for r in plannable)
                self.planner_status_label.setText(
                    f"AI 状态：多路线调度已启用（{len(plannable)} 条路线）"
                )
                self.planner_note_label.setText(f"可自动规划：{names}")
                self.planner_replan_btn.setEnabled(True)
            else:
                self.planner_status_label.setText(
                    "AI 状态：当前没有可自动规划的学习路线"
                )
                self.planner_note_label.setText(
                    "请在“学习路线”中启用自动规划或创建学习计划"
                )
                self.planner_replan_btn.setEnabled(False)
            self.planner_container.setVisible(True)
            return
        if self.daily_planner_service is None:
            self.planner_container.setVisible(False)
            return
        if self._planning_paused():
            self.planner_status_label.setText(
                f"AI 状态：{self._planning_route_name()} 自动规划已暂停"
            )
            self.planner_replan_btn.setEnabled(False)
            self.planner_container.setVisible(True)
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

        confirm = QMessageBox.question(
            self,
            "重新规划",
            "重新规划可能改变未开始任务。\n已完成与延期任务不受影响，确定继续？",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        today_str = self.current_date

        # ---- Phase D：全局多路线 Scheduler 路径 ----
        if self.scheduler is not None:
            plannable = []
            try:
                plannable = self.scheduler.plannable_routes(today_str)
            except Exception:  # noqa: BLE001
                plannable = []
            if not plannable:
                show_warning(self, "当前没有可自动规划的学习路线。")
                return
            # 只清理 today / generated / new / active；done、manual、cancelled 保留
            cleaned_ids = []
            for t in self.task_service.get_tasks_by_date(today_str):
                if (t.status == "active" and t.source == "generated"
                        and t.task_type == "new"):
                    self.task_service.repo.delete(t.id)
                    cleaned_ids.append(t.id)
            result = self.scheduler.generate(today_str, force=True)
            self.refresh(preserve_scroll=True)
            n = len(result.get("created", []))
            routes = len(result.get("plannable_route_ids", []))
            msg = f"重新规划完成：生成了 {n} 个任务（{routes} 条路线）"
            if cleaned_ids:
                msg += f"，移除了 {len(cleaned_ids)} 个旧生成任务"
            self.statusBar().showMessage(msg, 6000)
            return

        # ---- 单路线兼容路径 ----
        if self.daily_planner_service is None:
            show_warning(self, "AI 规划不可用，无法重新规划。")
            return
        planner = self.daily_planner_service.planner
        if planner is None or not planner.is_configured():
            show_warning(self, "AI 未配置，无法重新规划。")
            return
        if self._planning_paused():
            show_warning(
                self,
                f"{self._planning_route_name()} 自动规划已暂停；"
                "请先在“学习路线”中恢复自动规划。",
            )
            return

        # 清理今天可被重排的普通生成任务（active + generated）
        tasks = self.task_service.get_tasks_by_date(today_str)
        cleaned_ids = []
        for t in tasks:
            if t.status == "active" and t.source == "generated":
                self.task_service.repo.delete(t.id)
                cleaned_ids.append(t.id)

        # 对该日期重新生成（AI 优先，内部自动 fallback）。
        # force=True：即使当天已有 planner decision / cancelled 记录，也刷新计划；
        # cancelled topic 会由 Planner 的当天排除集自动跳过。
        result = self.daily_planner_service.generate_next_day_plan(
            add_days(today_str, -1), force=True
        )
        self.refresh(preserve_scroll=True)
        msg = f"重新规划完成：生成了 {len(result.get('created', []))} 个任务"
        if cleaned_ids:
            msg += f"，移除了 {len(cleaned_ids)} 个旧生成任务"
        self.statusBar().showMessage(msg, 5000)

    # ---------- 操作处理 ----------

    def _on_complete(self, task_id: int) -> None:
        self.task_service.complete_task(task_id)
        self.refresh(preserve_scroll=True)
        self.statusBar().showMessage("任务已完成", 3000)

    def _on_not_done(self, task_id: int) -> None:
        """未完成：先保存原因，再异步请求 AI 复核；AI 不可用不影响本地流程。"""
        task = self.task_service.get_task(task_id)
        reason = NotDoneDialog.get_reason(self, task.title)
        if reason is None:
            return  # 用户取消
        # 原因已保证非空（对话框内校验）
        self.task_service.mark_not_done(task_id, reason)
        self.refresh(preserve_scroll=True)
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

    def _release_assessment_worker(self, worker, task_id: int) -> None:
        """验收 worker 结束后：解除 in-flight 标记并释放引用。"""
        self._assessment_inflight.discard(task_id)
        self._release_worker(worker)

    def _release_worker(self, worker: AIReviewWorker) -> None:
        """AI 线程结束后从列表中移除引用。"""
        if worker in self._ai_workers:
            self._ai_workers.remove(worker)

    def _on_dialog_postpone(self, task_id: int) -> None:
        """用户在 AI 结果对话框中点击"延期到明天"。"""
        self._on_postpone(task_id)

    def _on_dialog_no_postpone(self, task_id: int) -> None:
        """用户选择不延期：保持 not_done，刷新界面。"""
        self.refresh(preserve_scroll=True)
        self.statusBar().showMessage("已保持未完成状态", 3000)

    def _on_postpone(self, task_id: int) -> None:
        task = self.task_service.postpone_task(task_id)
        self.refresh(preserve_scroll=True)
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
        """托盘“退出”：真正退出程序（集中清理后结束事件循环）。"""
        self._quit_requested = True
        self._shutdown()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        """点右上角 X = 最小化到系统托盘，程序继续运行；托盘“退出”走 quit_app。

        - 真正退出（_quit_requested）时：清理后接受关闭；
        - 托盘可用时点 X：只 hide，保留进程与托盘；
        - 无托盘可用时点 X：回退为“关闭即退出”，避免出现无入口的隐形进程。
        """
        if self._quit_requested or self._tray is None:
            self._shutdown()
            event.accept()
            return
        # 点 X：隐藏窗口（不退出，不清理托盘/AI 线程）
        event.ignore()
        self.hide()

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
        # 2b) 停止 AI 设置页的连接测试线程（如果仍在跑）
        settings_page = getattr(self, "ai_settings_page", None)
        if settings_page is not None:
            try:
                settings_page.profiles_panel.stop_test_worker()
            except Exception:  # noqa: BLE001 - 清理失败不阻断退出
                pass

        # 3) 关闭可能仍打开着的 AI 结果/验收对话框，避免残留顶级窗口
        #    （否则“最后一个窗口关闭”不会触发，QApplication 不退出）。
        app = QApplication.instance()
        if app is not None:
            for w in list(app.topLevelWidgets()):
                if isinstance(w, (AIReviewDialog, AssessmentDialog)) and w.isVisible():
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
        app.setQuitOnLastWindowClosed(False)  # 托盘常驻：X=隐藏，退出走 quit_app
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
