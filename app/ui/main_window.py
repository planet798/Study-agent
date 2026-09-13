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

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QIcon,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
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
from ..services.task_review_service import TaskReviewService
from ..services.task_service import TaskService
from ..utils.date_utils import add_days, today as _default_today
from .ai_worker import AIReviewWorker, AssessmentWorker
from .assessment_dialog import AssessmentDialog
from .dialogs import AIReviewDialog, NotDoneDialog
from .styles import APP_STYLE, apply_secondary_button_text
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
        assessment_service=None,
        assessment_repo=None,
        review_scheduler=None,
        extra_service=None,
        exploration_service=None,
        skill_service=None,
        jd_service=None,
        outcome_service=None,
        notes_service=None,
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
        # 复习调度服务（ReviewService，区别于上面的 review_service=TaskReviewService）
        self.review_scheduler = review_scheduler
        self.extra_service = extra_service
        self.exploration_service = exploration_service
        # Phase A~E 服务：可选；未传则对应职业面板隐藏（不回归旧行为）
        self.skill_service = skill_service
        self.jd_service = jd_service
        self.outcome_service = outcome_service
        self.notes_service = notes_service

        self._task_widgets: list[TaskWidget] = []
        self._quit_requested = False
        self._tray: QSystemTrayIcon | None = None
        self._ai_workers: list[AIReviewWorker] = []
        self._exploration_added = False

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
        """启动流程：日期切换 -> 生成今日到期复习 -> 加载今日任务。"""
        today_str = self.today_provider()
        self.date_service.process_date_transition(today_str)
        # 复习调度（幂等）：为今天到期的知识点生成复习任务
        if self.review_scheduler is not None:
            try:
                self.review_scheduler.generate_due_reviews(today=today_str)
            except Exception:  # noqa: BLE001 - 复习生成失败不影响启动
                pass
        self.current_date = today_str
        self.date_label.setText(today_str)
        self.refresh()

    def refresh(self) -> None:
        """重建今日页：新知识 / 复习 / 额外 / 课外探索 + 职业面板 + 统计。"""
        today_str = self.current_date
        tasks = self.task_service.get_tasks_by_date(today_str)
        self._today_tasks = tasks

        self._update_phase_info(today_str)
        self._update_planner_info()

        # 清空滚动区动态内容
        self._clear_dynamic_list()
        self._task_widgets.clear()
        self._exploration_added = False
        self._career_panel_added = False

        new_tasks = [t for t in tasks if t.task_type not in ("review", "extra")]
        review_tasks = [t for t in tasks if t.task_type == "review"]
        extra_tasks = [t for t in tasks if t.task_type == "extra"]

        # 1) 今日新知识
        self._add_section_header("今日新知识")
        for t in new_tasks:
            self._add_task_widget(t)

        # 2) 今日复习
        if self.review_scheduler is not None or review_tasks:
            self._add_section_header("今日复习")
            if review_tasks:
                for t in review_tasks:
                    self._add_task_widget(t)
            else:
                self._add_section_hint("今日暂无到期复习")

        # 3) 额外学习
        if self.extra_service is not None or extra_tasks:
            self._add_section_header("额外学习")
            self._add_extra_control(extra_tasks)
            for t in extra_tasks:
                self._add_task_widget(t)

        # 4) 课外探索
        if self.exploration_service is not None:
            self._add_section_header("课外探索")
            self._add_exploration()

        # Phase E：职业 / 技能 / JD 面板（可选注入，异常不崩溃）
        self._add_recent_focus_skills()
        self._add_skill_status()
        self._add_jd_panel()

        self.list_layout.addStretch()

        has_content = (
            bool(self._task_widgets)
            or self._exploration_added
            or self._career_panel_added
        )
        scroll_visible = (
            bool(tasks) or self._exploration_added or self._career_panel_added
        )
        self.empty_hint.setVisible(not has_content)
        self.scroll.setVisible(scroll_visible)

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
        widget = TaskWidget(task, assessment_label=label)
        widget.complete_requested.connect(self._on_complete)
        widget.not_done_requested.connect(self._on_not_done)
        widget.postpone_requested.connect(self._on_postpone)
        widget.assessment_requested.connect(self._on_start_assessment)
        self.list_layout.addWidget(widget)
        self._task_widgets.append(widget)

    def _add_extra_control(self, extra_tasks) -> None:
        """额外学习区域的额度提示 + 生成按钮。"""
        if self.extra_service is None:
            return
        used = len(extra_tasks)
        cap = int(getattr(self.extra_service, "max_daily_extra", 0))
        remaining = max(0, cap - used)
        row_w = QWidget()
        row = QHBoxLayout(row_w)
        row.setContentsMargins(0, 0, 0, 0)
        info = QLabel(f"已生成 {used} / {cap} 个，今日剩余额度 {remaining} 个")
        info.setObjectName("TaskMeta")
        btn = QPushButton("继续学习 / 生成额外任务")
        btn.setObjectName("SecondaryButton")
        apply_secondary_button_text(btn)
        btn.clicked.connect(self._on_generate_extra)
        row.addWidget(info)
        row.addStretch()
        row.addWidget(btn)
        self.list_layout.addWidget(row_w)

    def _on_generate_extra(self) -> None:
        if self.extra_service is None:
            return
        try:
            result = self.extra_service.generate_extra_tasks(today=self.current_date)
        except Exception as e:  # noqa: BLE001
            self.statusBar().showMessage(f"生成额外任务失败: {e}", 5000)
            return
        self.refresh()
        created = result.get("created", [])
        if created:
            msg = f"生成了 {len(created)} 个额外任务"
        elif result.get("skipped_duplicate"):
            msg = "额外任务已存在或今天已生成，未重复创建"
        else:
            msg = "今日额外额度已用完或没有可用学习来源"
        self.statusBar().showMessage(msg, 5000)

    def _add_exploration(self) -> None:
        """课外探索区域：展示已验证资源的卡片与打开链接按钮。"""
        svc = self.exploration_service
        try:
            context = svc.build_context(
                self.current_date, self.study_plan_service, self.assessment_repo
            )
            items = svc.recommend(context, limit=3)
        except Exception:  # noqa: BLE001
            self._add_section_hint("课外探索暂不可用")
            return
        if not items:
            self._add_section_hint("暂无匹配的课外探索资源")
            return
        self._exploration_added = True
        type_label = {"github": "GitHub", "leetcode": "LeetCode", "docs": "文档/资料"}
        for it in items:
            card = QWidget()
            cl = QVBoxLayout(card)
            cl.setContentsMargins(8, 6, 8, 6)
            head = QHBoxLayout()
            badge = QLabel(f"[{type_label.get(it.get('type'), it.get('type'))}] {it.get('title')}")
            badge.setObjectName("TaskTitle")
            head.addWidget(badge)
            head.addStretch()
            minutes = int(it.get("minutes") or 0)
            mlabel = QLabel(f"{minutes} 分钟" if minutes else "")
            mlabel.setObjectName("TaskMeta")
            head.addWidget(mlabel)
            cl.addLayout(head)
            why = QLabel(it.get("reason") or "")
            why.setWordWrap(True)
            why.setObjectName("TaskMeta")
            cl.addWidget(why)
            open_btn = QPushButton("打开链接")
            open_btn.setObjectName("SecondaryButton")
            apply_secondary_button_text(open_btn)
            open_btn.clicked.connect(
                lambda _=False, u=it.get("url", ""): self._open_exploration_url(u)
            )
            br = QHBoxLayout()
            br.addStretch()
            br.addWidget(open_btn)
            cl.addLayout(br)
            self.list_layout.addWidget(card)

    def _open_exploration_url(self, url: str) -> None:
        """打开课外资源链接（URL 只能来自已验证资源集合）。"""
        if not url:
            self.statusBar().showMessage("该资源没有可用链接", 3000)
            return
        if QDesktopServices.openUrl(QUrl(url)):
            self.statusBar().showMessage("已在浏览器中打开", 3000)
        else:
            self.statusBar().showMessage("无法打开链接", 3000)

    # ---------- Phase E：职业面板 ----------

    def _add_label(self, text: str, object_name: str = "TaskMeta",
                   word_wrap: bool = True) -> None:
        """往滚动区加一行文本。"""
        lbl = QLabel(text)
        lbl.setObjectName(object_name)
        lbl.setWordWrap(word_wrap)
        self.list_layout.addWidget(lbl)

    def _add_recent_focus_skills(self) -> None:
        """近期重点技能 + 当前阶段允许范围（来自 SkillService）。"""
        if self.skill_service is None:
            return
        self._career_panel_added = True
        self._add_section_header("近期重点技能")
        try:
            cands = self.skill_service.select_active_candidates(limit=5)
            phase_name = ""
            if self.study_plan_service is not None:
                ph = self.study_plan_service.get_current_phase(self.current_date)
                phase_name = ph.name if ph else ""
            if cands:
                self._add_label(
                    "重点：" + " ｜ ".join(d["name"] for d in cands)
                )
            else:
                self._add_label("暂无候选重点技能")
            scope = (
                f"当前阶段：{phase_name}；JD/技能只影响本阶段内优先级，"
                "Agent / RAG 等前置未满足的技能不会被提前安排。"
                if phase_name else
                "JD/技能只影响当前阶段内优先级，不越级安排前置未满足的技能。"
            )
            self._add_label(scope, object_name="PostponeWarning")
        except Exception:  # noqa: BLE001
            self._add_label("技能服务异常", object_name="QErrorMessage")

    def _add_skill_status(self) -> None:
        """技能状态：S/A 核心 + 状态 + 掌握证据 + 前置阻塞。"""
        if self.skill_service is None:
            return
        self._career_panel_added = True
        self._add_section_header("技能状态")
        try:
            skills = sorted(
                self.skill_service.skill_repo.list_all(),
                key=lambda s: (-{"S": 4, "A": 3, "B": 2, "C": 1}.get(
                    s.get("tier"), 0), s.get("status"), s.get("name")),
            )
        except Exception:  # noqa: BLE001
            self._add_label("技能服务异常", object_name="QErrorMessage")
            return
        if not skills:
            self._add_label("暂无技能数据")
            return
        shown = skills[:12]  # 展示上限，避免面板过长
        for s in shown:
            mastery = self.skill_service._mastery_for_skill(s)
            m_txt = f"{mastery:.2f}" if mastery is not None else "暂无验收证据"
            blocked = self.skill_service.is_blocked(s)
            prereq = "前置未满足" if blocked else "OK"
            self._add_label(
                f"{s.get('name')}（{s.get('tier') or '?'}级 · "
                f"{s.get('status')} · mastery:{m_txt} · 前置:{prereq}）"
            )

    def _add_jd_panel(self) -> None:
        """最新 JD / 岗位需求：列表 + 查看影响 + 添加 JD。"""
        if self.jd_service is None:
            return
        self._career_panel_added = True
        self._add_section_header("最新 JD / 岗位需求")
        try:
            jds = self.jd_service.jd_repo.list_all()
        except Exception:  # noqa: BLE001
            self._add_label("JD 服务异常", object_name="QErrorMessage")
            return
        if not jds:
            self._add_label("暂无已分析 JD")
        for jd in jds[-5:]:  # 展示最近 5 条
            parsed = jd.get("parsed") or {}
            row_w = QWidget()
            row = QHBoxLayout(row_w)
            row.setContentsMargins(0, 0, 0, 0)
            info = QLabel(
                f"{jd.get('company') or '—'}｜{jd.get('title') or '—'}｜"
                f"{jd.get('direction') or '—'}｜"
                f"{'实习' if parsed.get('intern') else '全职'}｜"
                f"{jd.get('uploaded_at') or '—'}"
            )
            info.setObjectName("TaskMeta")
            btn = QPushButton("查看影响")
            btn.setObjectName("SecondaryButton")
            apply_secondary_button_text(btn)
            btn.clicked.connect(
                lambda _=False, jd=jd: self._show_jd_detail(jd)
            )
            row.addWidget(info, 1)
            row.addWidget(btn)
            self.list_layout.addWidget(row_w)
        add_btn = QPushButton("添加 JD")
        add_btn.setObjectName("SecondaryButton")
        apply_secondary_button_text(add_btn)
        add_btn.clicked.connect(self._on_add_jd)
        ab = QHBoxLayout()
        ab.addStretch()
        ab.addWidget(add_btn)
        abw = QWidget()
        abw.setLayout(ab)
        self.list_layout.addWidget(abw)

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
                self.refresh()

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

        self.statusBar().showMessage("正在生成验收题…", 0)
        worker = AssessmentWorker(
            svc.start_assessment,
            args=(task.knowledge_point_id,),
            kwargs={"task_id": task.id},
            parent=self,
        )
        worker.succeeded.connect(self._on_assessment_ready)
        worker.failed.connect(self._on_assessment_failed)
        worker.finished.connect(lambda w=worker: self._release_worker(w))
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
            self.assessment_service, attempt, self.current_date, parent=self
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
        self.refresh()

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
