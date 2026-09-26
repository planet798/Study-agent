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
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ..services.date_service import DateService
from ..services.manual_task_service import ManualTaskService
from ..services.task_review_service import TaskReviewService
from ..services.task_service import TaskService
from ..utils.date_utils import add_days, today as _default_today
from ..database.schema import STATUS_ACTIVE, STATUS_CANCELLED, STATUS_NOT_DONE
from .ai_worker import (
    AIReviewWorker,
    AIRouteBuilderWorker,
    AssessmentWorker,
    run_start_assessment,
)
from .app_shell import PAGE_SPECS_BY_KEY, AppShell, PageKey
from .components.navigation import key_value
from .assessment_dialog import AssessmentDialog
from .dialogs import AIReviewDialog, NotDoneDialog
from .manual_task_dialog import KIND_TODO, AddLearningTaskDialog
from .task_widget import TaskWidget
from .today_page import TodayPage

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
        assessment_service=None,
        assessment_repo=None,
        skill_service=None,
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
        practice_capability_service=None,
        practice_readiness_service=None,
        theme_settings=None,
    ):
        super().__init__()
        # 主题偏好（QSettings；测试可注入隔离实例）；UI-2 runtime，不改 DB。
        self.theme_settings = theme_settings
        self.task_service = task_service
        self.date_service = date_service
        self.today_provider = today_provider or _default_today
        # AI 复核服务：可选，未配置/未传时本地功能完全正常
        self.review_service = review_service
        # 学习计划服务：可选，用于显示当前阶段；未传则隐藏该区域
        self.study_plan_service = study_plan_service
        # AI 动态规划服务：可选，用于”AI 今日规划“区域；未传则显示不可用
        self.daily_planner_service = daily_planner_service
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
        # Phase A~E 服务：可选；未传则对应职业面板隐藏（不回归旧行为）
        self.skill_service = skill_service
        self.outcome_service = outcome_service
        self.notes_service = notes_service
        # Phase C：学习路线服务（可选；不传则隐藏“学习路线”页）
        self.route_service = route_service
        self.route_plan_service = route_plan_service
        # Phase E：路线进度/掌握情况
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
        # Phase 5：Practice → Capability（可选）
        self.practice_capability_service = practice_capability_service
        # Phase 6：Practice Planner Feedback / Readiness（可选）
        self.practice_readiness_service = practice_readiness_service
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
        self.setMinimumSize(900, 620)
        self.resize(1180, 760)
        self.setWindowIcon(_tray_icon())

        # 启动主题顺序：读取 QSettings -> set_theme -> (下方 _apply_styles) apply，
        # 避免先显示 Light 再闪切 Dark。
        from .design.theme_manager import ThemeManager
        from .design.theme_preferences import load_theme_mode

        ThemeManager.instance().set_theme(load_theme_mode(self.theme_settings))

        self._build_ui()
        self._build_tray()
        self._apply_styles()
        self._on_startup()

    # ---------- UI 构建 ----------

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- App Shell: Sidebar + (PageHeader + QStackedWidget) ----
        self.app_shell = AppShell(parent=central)
        self.sidebar = self.app_shell.sidebar
        self.page_header = self.app_shell.page_header
        self.stack = self.app_shell.stack
        root.addWidget(self.app_shell)

        # legacy compatibility aliases（旧测试依赖；不再有可见的顶部导航）
        self.nav_layout = self.sidebar.items_layout
        self.nav_today_btn = self.sidebar.item(PageKey.TODAY)
        self.nav_routes_btn = self.sidebar.item(PageKey.ROUTES)
        self.nav_practice_btn = self.sidebar.item(PageKey.PRACTICE)
        self.nav_ai_btn = self.sidebar.item(PageKey.SETTINGS)
        self.sidebar.page_requested.connect(self._on_nav_requested)

        # ----- 今日页（View 已抽到 TodayPage，MainWindow 只做编排） -----
        self.today_page = TodayPage()
        self.stack.addWidget(self.today_page)

        # 日期进入统一 PageHeader；date_label 仍是同一个 label，日期业务逻辑不变。
        self.date_label = self.page_header.subtitle_label()

        # compatibility aliases（旧测试 / 旧代码依赖）
        self.scroll = self.today_page.scroll
        self.list_container = self.today_page.list_container
        self.list_layout = self.today_page.list_layout
        self.empty_hint = self.today_page.empty_hint
        self.route_filter_combo = self.today_page.route_filter_combo
        self.route_stats_label = self.today_page.route_stats_label
        self.phase_container = self.today_page.phase_container
        self.phase_label = self.today_page.phase_label
        self.phase_goal_label = self.today_page.phase_goal_label
        self.planner_container = self.today_page.planner_container
        self.planner_status_label = self.today_page.planner_status_label
        self.planner_note_label = self.today_page.planner_note_label
        self.planner_replan_btn = self.today_page.planner_replan_btn
        self.add_task_btn = self.today_page.add_task_btn

        self.today_page.add_task_requested.connect(self._on_add_learning_task)
        self.today_page.route_filter_changed.connect(
            self._on_route_filter_changed
        )
        self.today_page.replan_requested.connect(self._on_replan)
        self.routes_page_index = None
        self.ai_settings_page_index = None

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
                practice_capability_service=self.practice_capability_service,
                practice_readiness_service=self.practice_readiness_service,
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
                capability_service=self.practice_capability_service,
                readiness_service=self.practice_readiness_service,
            )
            self.stack.addWidget(self.practice_page)
            self.practice_page_index = self.stack.count() - 1
            self.nav_practice_btn.setEnabled(True)
        else:
            self.nav_practice_btn.setEnabled(False)

        # ----- 设置页（含外观，始终可用；AI 服务可选） -----
        from .ai_settings_page import AISettingsPage

        self.ai_settings_page = AISettingsPage(
            self.ai_config_service,
            self.prompt_registry,
            preview_service=self.prompt_preview_service,
            theme_settings=self.theme_settings,
        )
        self.stack.addWidget(self.ai_settings_page)
        self.ai_settings_page_index = self.stack.count() - 1
        self.nav_ai_btn.setEnabled(True)

        self.setCentralWidget(central)
        self.statusBar().showMessage("")
        # 初始选中 Today（页面索引 0），并同步 PageHeader。
        self._update_page_header(PageKey.TODAY)

    # ---------- 导航 / PageHeader ----------

    def _on_nav_requested(self, key: str) -> None:
        if key == PageKey.TODAY.value:
            self._switch_to_today()
        elif key == PageKey.ROUTES.value:
            self._switch_to_routes()
        elif key == PageKey.PRACTICE.value:
            self._switch_to_practice()
        elif key == PageKey.SETTINGS.value:
            self._switch_to_ai_settings()

    def _update_page_header(self, key) -> None:
        """更新统一 PageHeader，并同步 Sidebar selected 状态。"""
        spec = PAGE_SPECS_BY_KEY.get(key_value(key))
        if spec is None:
            return
        if key_value(key) == PageKey.TODAY.value:
            # Today subtitle = 当前日期；date_label 即同一个 label。
            self.page_header.set_title(spec.title)
            self.page_header.set_subtitle(getattr(self, "current_date", ""))
            self.page_header.set_icon(spec.icon)
        else:
            self.app_shell.set_page_header(key)
        self.sidebar.set_current(key)

    def _switch_to_today(self) -> None:
        self.stack.setCurrentIndex(0)
        self._update_page_header(PageKey.TODAY)

    def _switch_to_routes(self) -> None:
        if self.routes_page_index is None:
            self.statusBar().showMessage("学习路线不可用", 3000)
            return
        if self.routes_page is not None:
            self.routes_page.refresh()
        self.stack.setCurrentIndex(self.routes_page_index)
        self._update_page_header(PageKey.ROUTES)

    def _switch_to_ai_settings(self) -> None:
        if self.ai_settings_page_index is None:
            self.statusBar().showMessage("设置不可用", 3000)
            return
        if getattr(self, "ai_settings_page", None) is not None:
            self.ai_settings_page.refresh()
        self.stack.setCurrentIndex(self.ai_settings_page_index)
        self._update_page_header(PageKey.SETTINGS)

    def _switch_to_practice(self) -> None:
        if getattr(self, "practice_page_index", None) is None:
            self.statusBar().showMessage("实践项目不可用", 3000)
            return
        if getattr(self, "practice_page", None) is not None:
            self.practice_page.refresh()
        self.stack.setCurrentIndex(self.practice_page_index)
        self._update_page_header(PageKey.PRACTICE)

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
        """经 Design System ThemeManager 应用当前主题（默认 Light）。

        UI-1 只做最小接入：布局 / 导航 / 业务调用完全不变。
        """
        app = QApplication.instance()
        if app is not None:
            from .design.theme_manager import ThemeManager

            ThemeManager.instance().apply(app)

    # ---------- 启动 / 刷新 ----------

    def _on_startup(self) -> None:
        """启动流程：日期切换后加载今日任务。"""
        today_str = self.today_provider()
        self.date_service.process_date_transition(today_str)
        self.current_date = today_str
        self.date_label.setText(today_str)
        self.refresh()

    def refresh(self, preserve_scroll: bool = False) -> None:
        """重建今日页（学习任务 + 职业面板）。

        :param preserve_scroll: 同页面 mutation（完成/未完成/移除等）
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

        # Legacy review / extra rows remain in DB but are not shown on Today.
        new_tasks = [
            t for t in tasks
            if t.task_type not in ("review", "extra")
            and t.status != STATUS_CANCELLED
            and self._matches_route(t, selected_route)
        ]
        # 用户主动移除的任务：不进入“今日待执行任务”，仅折叠提示
        cancelled_tasks = [
            t for t in tasks
            if t.task_type != "review" and t.status == STATUS_CANCELLED
            and self._matches_route(t, selected_route)
        ]
        self._update_route_stats(tasks, selected_route)

        # Today Summary metrics（只从已获取的 tasks 推导，不新增 DB query）
        effective_tasks = [
            t for t in tasks
            if t.task_type != "review" and t.status != STATUS_CANCELLED
            and self._matches_route(t, selected_route)
        ]
        pending = [
            t for t in effective_tasks
            if t.status in (STATUS_ACTIVE, STATUS_NOT_DONE)
        ]
        pending_minutes = sum(int(t.estimated_minutes or 0) for t in pending)
        self.today_page.set_summary_metrics(len(pending), pending_minutes)

        # 今日学习
        self._add_section_header("今日学习")
        for t in new_tasks:
            self._add_task_widget(t)
        if cancelled_tasks:
            names = "、".join(t.title for t in cancelled_tasks)
            self._add_section_hint(
                f"已移除今日任务 {len(cancelled_tasks)} 个（不计入完成率）：{names}"
            )

        self.list_layout.addStretch()

        has_effective = any(
            t.task_type != "review" and t.status != STATUS_CANCELLED
            for t in tasks
        )
        self.empty_hint.setVisible(not bool(self._task_widgets))
        self.scroll.setVisible(has_effective)

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
        for delay in (0, 16, 60, 160, 400, 800):
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
            if t.task_type != "review" and t.status != STATUS_CANCELLED
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
        """验收成功后刷新今日页。"""
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
        """当前规划路线名；不隐式回退到旧默认路线。"""
        if self.route_service is None:
            return "当前学习路线"
        try:
            route_id = None
            if self.study_plan_service is not None:
                route_id = self.study_plan_service._resolved_route_id()
            if route_id is not None:
                route = self.route_service.route_repo.get(route_id)
                if route is not None:
                    return route.name
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
                self.today_page.planner_banner.set_variant("info")
            else:
                self.planner_status_label.setText(
                    "AI 状态：当前没有可自动规划的学习路线"
                )
                self.planner_note_label.setText(
                    "请在“学习路线”中启用自动规划或创建学习计划"
                )
                self.planner_replan_btn.setEnabled(False)
                self.today_page.planner_banner.set_variant("warning")
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
            self.today_page.planner_banner.set_variant("warning")
            self.planner_container.setVisible(True)
            return
        planner = self.daily_planner_service.planner
        if planner is not None and planner.is_configured():
            self.planner_status_label.setText("AI 状态：AI 已启用")
            self.planner_replan_btn.setEnabled(True)
            self.today_page.planner_banner.set_variant("info")
        else:
            self.planner_status_label.setText("AI 状态：AI 不可用")
            self.planner_replan_btn.setEnabled(False)
            self.today_page.planner_banner.set_variant("warning")
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
