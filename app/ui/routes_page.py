"""学习路线页面与详情的交互编排。

概览与详情视图独立组织；写操作沿用现有服务、确认与历史保护。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..database.learning_route_repository import ROUTE_TYPE_GROUP
from ..services.learning_route_service import RouteValidationError
from ..services.route_plan_service import RouteStructureError
from ..utils.date_utils import today as _default_today
from .dialogs import show_warning
from .components.button import SAButton
from .components.empty_state import SAEmptyState
from .components.progress_bar import SAProgressBar
from .components.status_badge import SAStatusBadge
from .components.section_header import SASectionHeader
from .components.tag import SATag
from .route_builder_dialogs import (
    AIRouteBuilderDialog,
    RouteDraftPreviewDialog,
    SkillPickerDialog,
)
from .ai_worker import AIRouteBuilderWorker
from .route_dialogs import (
    AddPhaseDialog,
    AddTopicDialog,
    CreateLearningRouteDialog,
    EditLearningRouteDialog,
    priority_text,
)
from .route_overview_widgets import (
    RouteActionsButton, RouteGroupHeading, RouteOverview, RouteOverviewRow,
)
from .route_scroll import RouteScrollKeeper
from .route_detail_sections import (
    RouteDetailSection, RouteDetailSummary, RoutePhaseSection, RouteTopicRow,
)
from .components.flow_layout import FlowWidget
from .design import spacing


def _secondary(text: str, on_click) -> QPushButton:
    """UI-3：Routes 区域按钮迁移到 SAButton（secondary）。"""
    btn = SAButton(text, variant="secondary", size="small")
    btn.clicked.connect(on_click)
    return btn


class RouteDetailDialog(QDialog):
    """路线详情：课程、证据与管理操作的弹窗编排。"""

    def __init__(self, route, route_service, route_plan_service, parent=None,
                 progress_service=None, today_provider=None,
                 ai_route_service=None, skill_service=None,
                 topic_learning_service=None, capability_service=None,
                 outcome_service=None, practice_service=None,
                 practice_capability_service=None,
                 practice_readiness_service=None):
        super().__init__(parent)
        self.route = route
        self.route_service = route_service
        self.route_plan_service = route_plan_service
        self.progress_service = progress_service
        self.today_provider = today_provider or _default_today
        self.ai_route_service = ai_route_service
        self.skill_service = skill_service
        self.topic_learning_service = topic_learning_service
        self.capability_service = capability_service
        self.outcome_service = outcome_service
        self.practice_service = practice_service
        self.practice_capability_service = practice_capability_service
        self.practice_readiness_service = practice_readiness_service
        self._ai_worker: AIRouteBuilderWorker | None = None
        self._closing = False
        self.setWindowTitle(f"路线：{route.name}")
        self.setModal(True)
        self.resize(880, 720)
        if self.screen() is not None:
            available = self.screen().availableGeometry()
            self.resize(min(880, int(available.width() * 0.9)),
                        min(720, int(available.height() * 0.9)))
        self._expanded_states: dict[str, bool] = {}
        self._default_phase_id = None

        root = QVBoxLayout(self)
        root.setContentsMargins(spacing.LG, spacing.LG, spacing.LG, spacing.LG)
        root.setSpacing(spacing.SM)

        self.title_label = QLabel(route.name)
        self.title_label.setObjectName("AppTitle")
        self.title_label.setWordWrap(True)
        root.addWidget(self.title_label)

        self.info_label = QLabel("")
        self.info_label.setObjectName("TaskMeta")
        self.info_label.setWordWrap(True)
        root.addWidget(self.info_label)

        self.action_row = QHBoxLayout()
        root.addLayout(self.action_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, spacing.SM, 0)
        self.body_layout.setSpacing(spacing.SM)
        self.scroll.setWidget(self.body)
        self._scroll_keeper = RouteScrollKeeper(self.scroll)
        root.addWidget(self.scroll, stretch=1)

        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(_secondary("关闭", self.accept))
        root.addLayout(btns)

        self.refresh()

    # ---------- 渲染 ----------

    def refresh(self) -> None:
        scroll_value = self._scroll_keeper.capture()
        route = self.route_service.get(self.route.id) or self.route
        self.route = route
        self.title_label.setText(route.name)
        self.setWindowTitle(f"路线：{route.name}")
        self.info_label.setText(
            f"目标：{route.goal or '—'}\n描述：{route.description or '—'}\n"
            f"优先级：{priority_text(route.priority)}\n"
            f"自动规划：{'已启用' if route.planning_enabled else '暂停'}　"
            f"状态：{'已归档' if route.is_archived else '进行中'}"
        )
        self.info_label.hide()  # historical alias, not a second visible overview
        self._clear_body()
        try:
            structure = self.route_plan_service.get_structure(route.id)
            structure_available = True
        except Exception:
            structure, structure_available = None, False
        self._rebuild_actions(route, structure is not None)
        rp = self._progress_for(route.id)
        progress = None
        if structure is not None:
            try:
                progress = self.route_plan_service.route_progress(route.id)
            except Exception:
                pass
        self._build_summary(route, structure, rp, progress, structure_available)
        self._build_route_information(route)
        if not structure_available:
            self.body_layout.addWidget(SAEmptyState(
                "课程数据暂不可用", "请稍后重新打开路线详情。"
            ))
        elif structure is None:
            empty = SAEmptyState("该路线还没有学习计划", "先建立课程阶段与知识点。")
            if not route.is_archived:
                actions = QWidget()
                actions_layout = QVBoxLayout(actions)
                actions_layout.setContentsMargins(0, 0, 0, 0)
                actions_layout.setSpacing(spacing.SM)
                # 空态的居中操作区需要完整的两行高度，避免换行后裁切按钮。
                actions_layout.addWidget(_secondary("创建手动学习计划", self._on_create_plan))
                actions_layout.addWidget(_secondary("AI 生成学习计划", self._on_ai_generate))
                empty.set_action(actions)
            self.body_layout.addWidget(empty)
        else:
            self._build_course_header(rp)
            done_ids = self._complete_topic_ids()
            self._default_phase_id = next(
                (p.id for p in structure.phases if any(t.id not in done_ids for t in p.topics)),
                None,
            )
            if not structure.phases:
                self.body_layout.addWidget(SAEmptyState(
                    "还没有课程阶段", "使用上方“添加阶段”完善学习计划。"
                ))
            for phase in structure.phases:
                self.body_layout.addWidget(self._phase_card(phase, done_ids))

        evidence = self._section("evidence", "掌握与能力", (
            f"{rp.assessment_evidence_count} 个已验收 · {rp.capability_evidence_count} 个有能力证据"
            if rp is not None else "数据暂不可用" if self.progress_service else "暂无验收数据"
        ))
        self._build_mastery_card(rp, evidence.body_layout)
        self._build_capability_card(rp, evidence.body_layout)
        self._add_skills_section()
        self._add_curriculum_gap_section()
        self._add_linked_projects_section()
        self._add_practice_blockers_section()
        self.body_layout.addStretch()
        self.body_layout.activate()
        self._scroll_keeper.restore(scroll_value)

    def _rebuild_actions(self, route, has_plan=True) -> None:
        self._clear_layout(self.action_row)
        actions = []
        if not route.is_archived:
            if has_plan:
                self.action_row.addWidget(_secondary("添加阶段", self._on_add_phase))
                actions.append(("ai", "AI 生成学习计划"))
            actions.extend([
                ("pause" if route.planning_enabled else "resume",
                 "暂停自动规划" if route.planning_enabled else "恢复自动规划"),
                ("archive", "归档路线"),
            ])
        else:
            actions.append(("restore", "恢复路线"))
        more = RouteActionsButton(actions)
        handlers = {"ai": self._on_ai_generate, "pause": self._on_pause,
                    "resume": self._on_resume, "archive": self._on_archive,
                    "restore": self._on_restore}
        more.requested.connect(lambda key: handlers[key]())
        self.action_row.addWidget(more)
        self.action_row.addStretch()

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                RouteDetailDialog._clear_layout(item.layout())

    def _clear_body(self) -> None:
        self._clear_layout(self.body_layout)

    def _remember_expanded(self, key: str, expanded: bool) -> None:
        self._expanded_states[key] = expanded
        self._scroll_keeper.cancel()

    def _section(self, key: str, title: str, summary="", *, expanded=False):
        section = RouteDetailSection(
            key, title, summary, expanded=self._expanded_states.get(key, expanded)
        )
        section.expanded_changed.connect(self._remember_expanded)
        self.body_layout.addWidget(section)
        return section

    def _build_summary(self, route, structure, rp, progress, available):
        phase, topic, next_activity = "尚未创建学习计划", "暂无知识点", "创建学习计划"
        if not available:
            phase = topic = next_activity = "数据暂不可用"
        elif structure is not None:
            done_ids = self._complete_topic_ids()
            topics = [(p, t) for p in structure.phases for t in p.topics]
            pending = [(p, t) for p, t in topics if t.id not in done_ids]
            if pending:
                current_phase, current_topic = pending[0]
                phase, topic = current_phase.name, current_topic.name
                if self.topic_learning_service:
                    try:
                        statuses = self.topic_learning_service.get_component_status(current_topic.id)
                        next_activity = next((s['label'] for s in statuses
                                              if s['required'] and not s['complete']), "查看课程学习安排")
                    except Exception:
                        next_activity = "学习活动暂不可用"
                else:
                    next_activity = "查看课程学习安排"
            elif topics:
                phase, topic, next_activity = "课程学习已完成", "课程知识点已完成", "查看掌握情况与能力证据"
            else:
                phase, topic, next_activity = "尚未添加知识点", "暂无知识点", "完善课程阶段与知识点"
        course_label, course_percent = "课程进度：尚未创建学习计划", None
        if structure is not None and rp is not None:
            # Detail coverage has always used the component-aware progress service.
            total, done = rp.topic_total, rp.topic_covered
            course_label = f"课程覆盖 {done}/{total}"
            course_percent = int(round(done * 100 / total)) if total else 0
        elif progress is not None:
            total, done = int(progress.get('total') or 0), int(progress.get('done') or 0)
            course_label = f"课程覆盖 {done}/{total}"
            course_percent = int(round(done * 100 / total)) if total else 0
        elif not available or structure is not None:
            course_label = "课程进度：数据暂不可用"
        unavailable = rp is None and self.progress_service is not None
        mastery = "数据暂不可用" if unavailable else "暂无验收数据"
        capability = "数据暂不可用" if unavailable else "暂无能力证据"
        if rp is not None:
            if rp.has_assessment:
                mastery = f"{rp.mastery_percent}%"
            if rp.capability_evidence_count:
                capability = f"{rp.capability_evidence_count} 个知识点"
        status = "archived" if route.is_archived else "active" if route.planning_enabled else "paused"
        self.body_layout.addWidget(RouteDetailSummary(
            goal=route.goal or '', phase=phase, topic=topic, next_activity=next_activity,
            status=status, course_label=course_label, course_percent=course_percent,
            mastery=mastery, capability=capability,
        ))

    def _progress_for(self, route_id):
        if self.progress_service is None:
            return None
        try:
            return self.progress_service.get_progress(
                route_id, self.today_provider()
            )
        except Exception:  # noqa: BLE001
            return None

    def _detail_block(self, title: str):
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, spacing.SM)
        layout.setSpacing(spacing.SM)
        layout.addWidget(SASectionHeader(title))
        return block, layout

    @staticmethod
    def _value_pair(lay, label: str, value: str) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        key = QLabel(label)
        key.setObjectName("SAValueLabel")
        val = QLabel(value if value else "—")
        val.setObjectName("SAValueStrong")
        val.setWordWrap(True)
        val.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(key)
        row.addWidget(val, stretch=1)
        lay.addLayout(row)

    # ---------- Overview ----------

    def _build_route_information(self, route) -> None:
        section = self._section("info", "路线信息")
        lay = section.body_layout
        self._value_pair(lay, "目标", route.goal or "—")
        self._value_pair(lay, "描述", route.description or "—")
        self._value_pair(lay, "优先级", priority_text(route.priority))
        self._value_pair(lay, "自动规划", "已启用" if route.planning_enabled else "暂停")
        self._value_pair(lay, "状态", "已归档" if route.is_archived else "进行中")

    def _build_course_header(self, rp) -> None:
        self.body_layout.addWidget(SASectionHeader("课程结构"))
        if rp is not None and (rp.activity_required_total or rp.activity_optional_total):
            text = f"必需学习活动 {rp.activity_required_done}/{rp.activity_required_total}"
            if rp.activity_optional_total:
                text += f"　可选学习活动 {rp.activity_optional_done}/{rp.activity_optional_total}"
            self.body_layout.addWidget(self._meta_label(text))
        elif rp is None and self.progress_service is not None:
            self.body_layout.addWidget(self._meta_label("学习活动数据暂不可用"))

    # ---------- Mastery ----------

    def _build_mastery_card(self, rp, target_layout=None) -> None:
        target = self.body_layout if target_layout is None else target_layout
        card, lay = self._detail_block("掌握情况")
        if rp is None:
            empty = QLabel("掌握数据暂不可用" if self.progress_service else "暂无掌握数据")
            empty.setObjectName("TaskMeta")
            lay.addWidget(empty)
            target.addWidget(card)
            return
        if rp.has_assessment:
            lay.addWidget(SAProgressBar(
                value=rp.mastery_percent, label="掌握度 Mastery"
            ))
        else:
            no_data = QLabel("掌握度：暂无验收数据")
            no_data.setObjectName("TaskMeta")
            lay.addWidget(no_data)
        last_assessed = max(
            (k.last_assessed_at for k in rp.knowledge if k.last_assessed_at),
            default=None,
        )
        for label, value in (
            ("已验收", f"{rp.assessment_evidence_count}"),
            ("已掌握", f"{rp.mastered_count}"),
            ("薄弱", f"{rp.weak_count}"),
            ("最近验收", last_assessed or "—"),
        ):
            self._value_pair(lay, label, value)
        target.addWidget(card)

    # ---------- Capability ----------

    def _build_capability_card(self, rp, target_layout=None) -> None:
        target = self.body_layout if target_layout is None else target_layout
        card, lay = self._detail_block("能力证据 Capability")
        if rp is None or rp.capability_evidence_count <= 0:
            empty = QLabel("能力证据数据暂不可用" if rp is None and self.progress_service else "暂无能力证据")
            empty.setObjectName("TaskMeta")
            lay.addWidget(empty)
        else:
            summary = QLabel(
                f"有能力证据：{rp.capability_evidence_count} 个知识点"
            )
            summary.setObjectName("TaskMeta")
            lay.addWidget(summary)
            dist = QHBoxLayout()
            dist.setSpacing(6)
            for level in sorted(rp.capability_level_counts):
                count = int(rp.capability_level_counts.get(level, 0) or 0)
                if count > 0:
                    dist.addWidget(SATag(f"L{level} × {count}", "info"))
            dist.addStretch()
            lay.addLayout(dist)

        if rp is not None and rp.knowledge:
            lay.addWidget(SASectionHeader("知识状态"))
            for ks in rp.knowledge:
                self._add_knowledge_row(lay, ks)
        target.addWidget(card)

    def _add_knowledge_row(self, lay, ks) -> None:
        row = QHBoxLayout()
        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(2)
        title = QLabel(f"{ks.name}　{ks.status}")
        title.setObjectName("TaskTitle")
        title.setWordWrap(True)
        title.setTextFormat(Qt.TextFormat.PlainText)
        info.addWidget(title)

        mastery_txt = (
            "暂无验收" if ks.mastery is None else f"{ks.mastery_percent}%"
        )
        info.addWidget(self._meta_label(f"Mastery：{mastery_txt}"))

        if ks.has_capability_evidence:
            cap_txt = f"L{ks.capability_level} · {ks.capability_label}"
        else:
            cap_txt = "暂无能力证据"
        info.addWidget(self._meta_label(f"Capability：{cap_txt}"))

        if ks.weak_points:
            info.addWidget(self._meta_label(
                f"弱点：{'、'.join(ks.weak_points[:3])}"
            ))
        row.addLayout(info, stretch=1)

        if self.capability_service is not None and ks.knowledge_point_id:
            row.addWidget(_secondary(
                "查看证据", lambda _=False, k=ks: self._on_view_evidence(k)
            ))
            if self._needs_experiment_record(ks):
                row.addWidget(_secondary(
                    "记录实验成果",
                    lambda _=False, k=ks: self._on_record_experiment(k),
                ))
        lay.addLayout(row)

    @staticmethod
    def _meta_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("TaskMeta")
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.PlainText)
        return lbl

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("SectionTitle")
        return lbl

    def _add_linked_projects_section(self) -> None:
        if self.practice_service is None:
            return
        try:
            projects = self.practice_service.list_by_route(self.route.id)
        except Exception:
            section = self._section("projects", "关联实践项目", "暂不可用")
            section.body_layout.addWidget(self._meta_label("项目数据暂不可用"))
            return
        section = self._section("projects", "关联实践项目", f"{len(projects)} 个")
        for project in projects:
            section.body_layout.addWidget(self._meta_label(project['name']))
        if not projects:
            section.body_layout.addWidget(self._meta_label("尚未关联实践项目"))

    def _add_practice_blockers_section(self) -> None:
        if self.practice_readiness_service is None:
            return
        try:
            blockers = self.practice_readiness_service.list_route_blockers(
                self.route.id, self.today_provider()
            )
        except Exception:
            section = self._section("blockers", "项目学习需求", "暂不可用")
            section.body_layout.addWidget(self._meta_label("学习需求数据暂不可用"))
            return
        section = self._section("blockers", "项目学习需求", f"{len(blockers)} 项")
        for st in blockers:
            nxt = st.next_activity_label or st.reason_label
            section.body_layout.addWidget(self._meta_label(
                f"{st.topic_name}　{st.current_capability_label} → "
                f"{st.target_capability_label}　下一步：{nxt}"
            ))
        if not blockers:
            section.body_layout.addWidget(self._meta_label("暂无项目学习需求"))

    # ---------- Phase 3：Capability ----------

    def _needs_experiment_record(self, ks) -> bool:
        """该 kp 是否已有 done experiment task 但尚无 EXPERIMENT evidence。"""
        if self.capability_service is None or ks.knowledge_point_id is None:
            return False
        if self.capability_service.get_current_level(
            ks.knowledge_point_id
        ) >= 4:
            return False
        conn = getattr(self.route_plan_service.task_repo, "conn", None)
        if conn is None:
            return False
        row = conn.execute(
            "SELECT 1 FROM tasks WHERE knowledge_point_id = ? "
            "AND status='done' AND learning_activity_kind='experiment' LIMIT 1",
            (int(ks.knowledge_point_id),),
        ).fetchone()
        return row is not None

    def _on_view_evidence(self, ks) -> None:
        from .capability_dialog import CapabilityEvidenceDialog

        CapabilityEvidenceDialog(
            ks.name, ks.knowledge_point_id, self.capability_service,
            practice_capability_service=self.practice_capability_service,
            parent=self,
        ).exec()

    def _on_record_experiment(self, ks) -> None:
        from .capability_dialog import ExperimentOutcomeDialog

        dlg = ExperimentOutcomeDialog(
            ks.name, ks.knowledge_point_id,
            self.outcome_service, self.route_plan_service.task_repo,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _phase_card(self, phase, done_ids) -> QWidget:
        topics = [RouteTopicRow(
            id=t.id, name=t.name, minutes=t.estimated_minutes,
            complete=t.id in done_ids, activities=self._activity_chips(t.id)
            if self.topic_learning_service is not None else "",
            has_profile=self.topic_learning_service is not None,
        ) for t in phase.topics]
        key = f"phase:{phase.id}"
        section = RoutePhaseSection(
            phase.id, f"阶段 {phase.order_index or '—'}：{phase.name}", phase.goals or "",
            topics, archived=self.route.is_archived,
            expanded=self._expanded_states.get(key, phase.id == self._default_phase_id),
        )
        section.expanded_changed.connect(self._remember_expanded)
        by_id = {t.id: t for t in phase.topics}
        def requested(action, entity_id):
            if action == "add_topic":
                self._on_add_topic(phase)
            elif action == "delete_phase":
                self._on_delete_phase(phase)
            elif action == "delete_topic":
                self._on_delete_topic(by_id[entity_id])
            elif action == "edit_profile":
                self._on_edit_profile(by_id[entity_id])
        section.requested.connect(requested)
        return section

    def _complete_topic_ids(self) -> set:
        if self.topic_learning_service is not None and not self.route.is_archived:
            try:
                return set(
                    self.topic_learning_service.curriculum_complete_topic_ids()
                )
            except Exception:  # noqa: BLE001
                pass
        return set(self.route_plan_service.done_topic_ids())

    def _activity_chips(self, topic_id: int) -> str:
        try:
            statuses = self.topic_learning_service.get_component_status(topic_id)
        except Exception:  # noqa: BLE001
            return "学习活动暂不可用"
        parts = []
        for s in statuses:
            if s["complete"]:
                status = "已完成"
            elif s["required"]:
                status = "必需"
            else:
                status = "可选"
            parts.append(f"{s['label']} · {status}")
        return "　".join(parts)

    def _on_edit_profile(self, topic) -> None:
        from .topic_learning_dialog import TopicLearningProfileDialog

        dlg = TopicLearningProfileDialog(
            topic, self.topic_learning_service, parent=self
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    # ---------- 操作 ----------

    def _on_create_plan(self) -> None:
        try:
            self.route_plan_service.ensure_manual_plan(
                self.route.id, self.route.name
            )
        except RouteStructureError as e:  # noqa: BLE001
            show_warning(self, str(e))
        self.refresh()

    def _on_add_phase(self) -> None:
        structure = self.route_plan_service.get_structure(self.route.id)
        if structure is None:
            show_warning(self, "请先创建学习计划")
            return
        dlg = AddPhaseDialog(
            default_order=len(structure.phases) + 1, parent=self
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dlg.result_payload()
        try:
            self.route_plan_service.add_phase(
                self.route.id, name=payload["name"], goal=payload["goal"],
                order_index=payload["order_index"],
            )
        except RouteStructureError as e:  # noqa: BLE001
            show_warning(self, str(e))
        self.refresh()

    def _on_add_topic(self, phase) -> None:
        dlg = AddTopicDialog(
            default_order=len(phase.topics) + 1, parent=self
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dlg.result_payload()
        try:
            self.route_plan_service.add_topic(
                self.route.id, phase.id,
                name=payload["name"],
                description=payload["description"],
                estimated_minutes=payload["estimated_minutes"],
                priority=payload["priority"],
                order_index=payload["order_index"],
            )
        except RouteStructureError as e:  # noqa: BLE001
            show_warning(self, str(e))
        self.refresh()

    def _on_delete_topic(self, topic) -> None:
        if self.route_plan_service.topic_has_history(topic.id):
            show_warning(self, "该知识点已有学习记录，不能直接删除。")
            return
        if not self._confirm_delete_dialog(f"确认删除知识点「{topic.name}」？"):
            return
        try:
            self.route_plan_service.delete_topic(topic.id, route_id=self.route.id)
        except RouteStructureError as e:  # noqa: BLE001
            show_warning(self, str(e))
        self.refresh()

    def _confirm_delete_dialog(self, text: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("删除知识点")
        box.setText(text)
        yes = box.addButton("删除", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def _on_delete_phase(self, phase) -> None:
        try:
            self.route_plan_service.delete_phase(phase.id)
        except RouteStructureError as e:  # noqa: BLE001
            show_warning(self, str(e))
        self.refresh()

    def _on_pause(self) -> None:
        self.route_service.pause_planning(self.route.id)
        self.refresh()

    def _on_resume(self) -> None:
        try:
            self.route_service.resume_planning(self.route.id)
        except RouteValidationError as e:  # noqa: BLE001
            show_warning(self, str(e))
        self.refresh()

    def _on_archive(self) -> None:
        if not self._confirm_archive_dialog():
            return
        try:
            self.route_service.archive_route(self.route.id)
        except RouteValidationError as e:  # noqa: BLE001
            show_warning(self, str(e))
        self.refresh()

    def _confirm_archive_dialog(self) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("归档路线")
        box.setText(
            "归档后该路线不再参与新的学习规划，历史学习记录不会删除。"
        )
        yes = box.addButton("归档", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def _on_restore(self) -> None:
        self.route_service.restore_route(self.route.id)
        self.refresh()

    # ---------- Phase F：关联技能 / 课程缺口 / AI 生成 ----------

    def _route_topic_ids(self) -> set[int]:
        try:
            return {
                t.id for t in self.route_plan_service.plan_repo.list_topics_by_route(
                    self.route.id
                )
            }
        except Exception:  # noqa: BLE001
            return set()

    def _skill_linked_in_route(self, skill: dict) -> bool:
        linked = set(skill.get("linked_topics") or [])
        return bool(linked & self._route_topic_ids())

    def _add_skills_section(self) -> None:
        if self.skill_service is None:
            return
        route_repo = self.route_service.route_repo
        try:
            skill_ids = route_repo.list_skill_ids(self.route.id)
            skills = [s for sid in skill_ids
                      if (s := self.skill_service.skill_repo.get(sid)) is not None]
        except Exception:
            section = self._section("skills", "关联技能", "暂不可用")
            section.body_layout.addWidget(self._meta_label("技能数据暂不可用"))
            return
        section = self._section("skills", "关联技能", f"{len(skills)} 个")
        lay = section.body_layout
        if not skills:
            lay.addWidget(self._meta_label("尚未关联技能"))
        for skill in skills:
            row = QHBoxLayout()
            row.addWidget(self._meta_label(f"{skill['name']}（{skill.get('tier', '')}级）"), 1)
            if not self.route.is_archived:
                row.addWidget(_secondary("移除关联", lambda _=False, sk=skill: self._on_remove_skill(sk)))
            lay.addLayout(row)
        if not self.route.is_archived:
            lay.addWidget(_secondary("关联已有技能", self._on_assign_skill))

    def _on_assign_skill(self) -> None:
        route_repo = self.route_service.route_repo
        try:
            bound = set(route_repo.list_skill_ids(self.route.id))
            names = [
                s["name"] for s in self.skill_service.skill_repo.list_all()
                if s["id"] not in bound
            ]
        except Exception:  # noqa: BLE001
            names = []
        if not names:
            show_warning(self, "没有可关联的新技能。")
            return
        dlg = SkillPickerDialog(names, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.selected:
            return
        skill = self.skill_service.skill_repo.get_by_name(dlg.selected)
        if skill is not None:
            route_repo.assign_skill(self.route.id, skill["id"])
        self.refresh()

    def _on_remove_skill(self, skill: dict) -> None:
        if self.skill_service is not None:
            fresh = self.skill_service.skill_repo.get(skill["id"])
            if fresh is not None:
                skill = fresh
        if self._skill_linked_in_route(skill):
            show_warning(
                self,
                "该技能与当前路线课程存在 Topic 关联，已阻止解除关联。",
            )
            return
        route_repo = self.route_service.route_repo
        route_repo.unassign_skill(self.route.id, skill["id"])
        self.refresh()

    def _add_curriculum_gap_section(self) -> None:
        if self.skill_service is None:
            return
        try:
            gaps = self.skill_service.curriculum_gap_skills(route_id=self.route.id)
        except Exception:
            section = self._section("gaps", "课程缺口", "暂不可用")
            section.body_layout.addWidget(self._meta_label("课程缺口数据暂不可用"))
            return
        section = self._section("gaps", "课程缺口", f"{len(gaps)} 项")
        lay = section.body_layout
        if not gaps:
            lay.addWidget(self._meta_label("暂无课程缺口"))
        for gap in gaps:
            row = QHBoxLayout()
            freq = float(gap.get('frequency_30d') or 0.0) * 100
            row.addWidget(self._meta_label(
                f"{gap['skill']}　近30天目标岗位需求：{freq:.1f}%　当前路线暂无 Topic"
            ), 1)
            if not self.route.is_archived:
                row.addWidget(_secondary("添加知识点", lambda _=False, name=gap['skill']: self._on_gap_add_topic(name)))
            lay.addLayout(row)

    def _on_gap_add_topic(self, skill_name: str) -> None:
        structure = self.route_plan_service.get_structure(self.route.id)
        if structure is None or not structure.phases:
            show_warning(self, "请先创建学习计划与阶段。")
            return
        from .route_dialogs import AddTopicDialog

        dlg = AddTopicDialog(
            default_order=len(structure.phases[0].topics) + 1, parent=self
        )
        dlg.title_edit.setText(skill_name)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dlg.result_payload()
        try:
            self.route_plan_service.add_topic(
                self.route.id, structure.phases[0].id, **payload
            )
        except RouteStructureError as e:  # noqa: BLE001
            show_warning(self, str(e))
        self.refresh()

    # ---------- AI 生成 ----------

    def _ai_context_data(self) -> tuple[list, dict | None]:
        """主线程收集纯数据：route_skills + 该路线 skill 的 market signal。"""
        skills: list[dict] = []
        market_payload: dict | None = None
        if self.skill_service is None:
            return skills, market_payload
        try:
            market = self.skill_service.refresh_market(self.today_provider())
        except Exception:  # noqa: BLE001
            market = None
        try:
            bound = self.route_service.route_repo.list_skill_ids(self.route.id)
        except Exception:  # noqa: BLE001
            bound = []
        market_skills: dict = {}
        for sid in bound:
            s = self.skill_service.skill_repo.get(sid)
            if s is None:
                continue
            rec = (market or {}).get("skills", {}).get(s["name"])
            freq = float(rec.get("freq30") or 0.0) if rec else 0.0
            skills.append({"name": s["name"], "frequency_30d": freq})
            if freq > 0:
                market_skills[s["name"]] = {
                    "freq30": freq,
                    "mention_30d": int(rec.get("mention_30d") or 0),
                }
        if market_skills:
            market_payload = {"skills": market_skills}
        return skills, market_payload

    def _on_ai_generate(self) -> None:
        if self._closing or self._ai_worker is not None:
            return
        if self.ai_route_service is None or \
                not self.ai_route_service.is_configured():
            show_warning(self, "AI 未配置，无法生成学习路线；可继续手动创建。")
            return
        mode, reason = self.route_plan_service.ai_plan_mode(self.route.id)
        if mode == "blocked":
            show_warning(self, reason)
            return
        dlg = AIRouteBuilderDialog(
            self.route.name, self.route.goal or "", parent=self
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        context = dlg.result_payload()
        route_skills, market = self._ai_context_data()
        worker = AIRouteBuilderWorker(
            self.ai_route_service, context,
            route_skills=route_skills, market=market, parent=self,
        )
        self._ai_worker = worker
        worker.succeeded.connect(self._on_worker_succeeded)
        worker.failed.connect(self._on_worker_failed)
        worker.finished.connect(self._on_worker_finished)
        try:
            worker.start()
        except Exception:
            self._ai_worker = None
            worker.deleteLater()
            raise

    def _on_worker_succeeded(self, draft) -> None:
        if not self._closing and self.sender() is self._ai_worker:
            self._on_draft_ready(draft)

    def _on_worker_failed(self, message: str) -> None:
        if not self._closing and self.sender() is self._ai_worker:
            self._on_draft_failed(message)

    def _on_worker_finished(self) -> None:
        worker = self.sender()
        if worker is self._ai_worker:
            self._ai_worker = None
            worker.deleteLater()

    def drain_worker(self) -> None:
        """Wait for the dialog-owned AI call; interruption does not cancel HTTP."""
        self._closing = True
        self._scroll_keeper.cancel()
        worker = self._ai_worker
        if worker is None:
            return
        for signal, slot in ((worker.succeeded, self._on_worker_succeeded),
                             (worker.failed, self._on_worker_failed),
                             (worker.finished, self._on_worker_finished)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        if worker.isRunning():
            worker.requestInterruption()
            worker.wait()
        self._ai_worker = None
        worker.deleteLater()

    def done(self, result):  # noqa: N802 - accept/reject can hide the dialog
        self.drain_worker()
        super().done(result)

    def closeEvent(self, event):  # noqa: N802 - Qt API
        self.drain_worker()
        super().closeEvent(event)

    def showEvent(self, event):  # noqa: N802 - a hidden dialog may reopen
        if self._ai_worker is None:
            self._closing = False
        super().showEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.DeferredDelete:
            self.drain_worker()
        return super().event(event)

    def _on_draft_failed(self, message: str) -> None:
        if not self._closing:
            show_warning(self, f"AI 生成失败：{message}")

    def _on_draft_ready(self, draft) -> None:
        if self._closing:
            return
        mode, reason = self.route_plan_service.ai_plan_mode(self.route.id)
        if mode == "blocked":
            show_warning(self, reason)
            return
        preview = RouteDraftPreviewDialog(draft, mode=mode, parent=self)
        if preview.exec() != QDialog.DialogCode.Accepted or self._closing:
            return
        new_draft = preview.draft()
        from ..ai.schemas import AIRouteDraft

        new_draft = AIRouteDraft(
            route_name=self.route.name,
            plan_name=new_draft.plan_name,
            summary=draft.summary,
            phases=new_draft.phases,
        )
        try:
            self.route_plan_service.create_plan_from_draft(
                self.route.id, new_draft,
                replace_empty=preview.replace_empty,
            )
        except RouteStructureError as e:  # noqa: BLE001
            show_warning(self, str(e))
            return
        self.refresh()


class LearningRoutesPage(QWidget):
    """学习路线总览页面。"""

    def __init__(self, route_service, route_plan_service=None, parent=None,
                 progress_service=None, today_provider=None,
                 ai_route_service=None, skill_service=None,
                 topic_learning_service=None, capability_service=None,
                 outcome_service=None, practice_service=None,
                 practice_capability_service=None,
                 practice_readiness_service=None):
        super().__init__(parent)
        self.route_service = route_service
        self.route_plan_service = route_plan_service
        self.progress_service = progress_service
        self.today_provider = today_provider or _default_today
        self.ai_route_service = ai_route_service
        self.skill_service = skill_service
        self.topic_learning_service = topic_learning_service
        self.capability_service = capability_service
        self.outcome_service = outcome_service
        self.practice_service = practice_service
        self.practice_capability_service = practice_capability_service
        self.practice_readiness_service = practice_readiness_service
        self.show_archived = False
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.addStretch()
        self.create_btn = _secondary("＋ 新建学习路线", self._on_create_route)
        self.archive_toggle_btn = _secondary("查看已归档", self._on_toggle_archived)
        head.addWidget(self.create_btn)
        head.addWidget(self.archive_toggle_btn)
        root.addLayout(head)

        hint = QLabel(
            "路线组织学习内容；启用自动规划后，系统按课程要求与每日预算安排学习。"
        )
        hint.setObjectName("TaskMeta")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(8)
        self.scroll.setWidget(self.list_container)
        self._scroll_keeper = RouteScrollKeeper(self.scroll)
        root.addWidget(self.scroll, stretch=1)

    # ---------- 渲染 ----------

    def refresh(self) -> None:
        scroll_value = self._scroll_keeper.capture()
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        roots = self.route_service.route_repo.list_roots()
        active_roots = [r for r in roots if not r.is_archived]
        active_route_count = 0

        for root in active_roots:
            if root.route_type == ROUTE_TYPE_GROUP:
                children = self.route_service.route_repo.list_children(root.id)
                active_children = [c for c in children if not c.is_archived]
                self.list_layout.addWidget(self._group_card(root, active_children))
                for child in active_children:
                    self.list_layout.addWidget(self._route_card(child))
                active_route_count += len(active_children)
            else:
                self.list_layout.addWidget(self._route_card(root))
                active_route_count += 1

        if not active_route_count:
            empty = SAEmptyState(
                "还没有学习路线", "新建路线，为学习内容安排阶段与知识点。",
                action=_secondary("新建学习路线", self._on_create_route),
            )
            self.list_layout.addWidget(empty)
        self.create_btn.setVisible(bool(active_route_count))

        if self.show_archived:
            self.list_layout.addWidget(self._section_label("已归档"))
            archived = [
                r for r in self.route_service.route_repo.list_all()
                if r.is_archived
            ]
            if archived:
                for r in archived:
                    self.list_layout.addWidget(self._route_card(r))
            else:
                empty = QLabel("暂无已归档路线")
                empty.setObjectName("EmptyHint")
                self.list_layout.addWidget(empty)

        self.list_layout.addStretch()
        self.list_layout.activate()
        self._scroll_keeper.restore(scroll_value)

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("SectionTitle")
        return lbl

    def _group_card(self, route, active_children) -> QWidget:
        summary = f"类型：分组　活跃路线：{len(active_children)}"
        if self.progress_service is not None:
            try:
                gs = self.progress_service.group_summary(route.id)
                summary = (
                    f"类型：分组　子路线：{gs['children_total']}（活跃 {gs['active']} / "
                    f"暂停 {gs['paused']} / 已归档 {gs['archived']}）"
                )
            except Exception:  # optional summary must not prevent navigation
                summary += "　统计暂不可用"
        heading = RouteGroupHeading(route.name, summary, route.goal or "")
        heading.requested.connect(lambda _key, r=route: self._archive(r))
        return heading

    def _route_card(self, route) -> QWidget:
        phase, topic, next_activity = "尚未创建学习计划", "暂无知识点", "创建学习计划"
        course_percent = None
        course_label = "课程进度：尚未创建学习计划"
        try:
            structure = self._structure(route.id) if self.route_plan_service is None else \
                self.route_plan_service.get_structure(route.id)
            if structure is not None:
                done_ids = self._complete_topic_ids()
                topics = [(p, t) for p in structure.phases for t in p.topics]
                pending = [(p, t) for p, t in topics if t.id not in done_ids]
                if pending:
                    current_phase, current_topic = pending[0]
                    phase, topic = current_phase.name, current_topic.name
                    next_activity = self._next_activity_label(current_topic.id) or "查看课程学习安排"
                elif topics:
                    phase, topic, next_activity = "课程学习已完成", "课程知识点已完成", "查看掌握情况与能力证据"
                else:
                    phase, topic, next_activity = "尚未添加知识点", "暂无知识点", "完善课程阶段与知识点"
                progress = self.route_plan_service.route_progress(route.id)
                total, done = int(progress.get("total") or 0), int(progress.get("done") or 0)
                course_label = f"课程进度 {done}/{total}"
                course_percent = int(round(done * 100 / total)) if total else 0
        except Exception:  # render failure honestly rather than as zero progress
            phase = topic = next_activity = "数据暂不可用"
            course_label = "课程进度：数据暂不可用"

        rp = self._route_progress(route.id)
        unavailable = rp is None and self.progress_service is not None
        mastery = "数据暂不可用" if unavailable else "暂无验收数据"
        capability = "数据暂不可用" if unavailable else "暂无能力证据"
        if rp is not None:
            if rp.has_assessment:
                mastery = f"{rp.mastery_percent}%"
            if rp.capability_evidence_count:
                capability = f"{rp.capability_evidence_count} 个知识点"
        status = "archived" if route.is_archived else ("active" if route.planning_enabled else "paused")
        actions = [("restore", "恢复路线")] if route.is_archived else [
            ("edit", "调整路线"),
            ("pause" if route.planning_enabled else "resume",
             "暂停自动规划" if route.planning_enabled else "恢复自动规划"),
            ("archive", "归档路线"),
        ]
        row = RouteOverviewRow(RouteOverview(
            name=route.name, key=str(getattr(route, "route_key", "") or "").split("_")[0],
            status=status, phase=phase, topic=topic, next_activity=next_activity,
            priority=priority_text(route.priority), course_label=course_label,
            course_percent=course_percent, mastery=mastery, capability=capability,
        ), actions)
        handlers = {"open": self._open_detail, "edit": self._edit, "pause": self._pause,
                    "resume": self._resume, "archive": self._archive, "restore": self._restore}
        row.requested.connect(lambda key, r=route: handlers[key](r))
        return row

    # ---------- Route card helpers ----------

    def _structure(self, route_id):
        if self.route_plan_service is None:
            return None
        try:
            return self.route_plan_service.get_structure(route_id)
        except Exception:  # noqa: BLE001
            return None

    def _route_progress(self, route_id):
        if self.progress_service is None:
            return None
        try:
            return self.progress_service.get_progress(
                route_id, self.today_provider()
            )
        except Exception:  # noqa: BLE001
            return None

    def _next_activity_label(self, topic_id) -> str:
        if self.topic_learning_service is None or topic_id is None:
            return ""
        try:
            statuses = self.topic_learning_service.get_component_status(topic_id)
        except Exception:  # noqa: BLE001
            return ""
        for s in statuses:
            if s.get("required") and not s.get("complete"):
                return s.get("label") or ""
        return ""

    @staticmethod
    def _route_capability_distribution(rp) -> list[tuple[int, int]]:
        """返回 (level, count)，只用 RouteProgress 提供的 distribution。

        不做任何 route-level 聚合（不取最高/最低/平均/第一个 KP）。
        """
        if rp is None:
            return []
        counts = getattr(rp, "capability_level_counts", {}) or {}
        return [
            (int(level), int(counts.get(level, 0) or 0))
            for level in sorted(counts)
            if int(counts.get(level, 0) or 0) > 0
        ]

    def _complete_topic_ids(self) -> set:
        if self.topic_learning_service is not None:
            try:
                return set(
                    self.topic_learning_service.curriculum_complete_topic_ids()
                )
            except Exception:  # noqa: BLE001
                pass
        return set(self.route_plan_service.done_topic_ids())

    # ---------- 操作 ----------

    def _on_create_route(self) -> None:
        groups = [
            r for r in self.route_service.route_repo.list_all()
            if r.route_type == ROUTE_TYPE_GROUP and not r.is_archived
        ]
        dlg = CreateLearningRouteDialog(
            groups=[{"id": g.id, "name": g.name} for g in groups], parent=self
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dlg.result_payload()
        try:
            if payload["is_group"]:
                self.route_service.create_group(
                    payload["name"], description=payload["description"],
                    goal=payload["goal"], priority=payload["priority"],
                )
            else:
                self.route_service.create_learning_route(
                    payload["name"], parent_id=payload["parent_id"],
                    description=payload["description"], goal=payload["goal"],
                    priority=payload["priority"],
                    planning_enabled=payload["planning_enabled"],
                )
        except RouteValidationError as e:  # noqa: BLE001
            show_warning(self, str(e))
            return
        self.refresh()

    def _on_toggle_archived(self) -> None:
        self.show_archived = not self.show_archived
        self.archive_toggle_btn.setText(
            "隐藏已归档" if self.show_archived else "查看已归档"
        )
        self.refresh()

    def _open_detail(self, route) -> None:
        dlg = RouteDetailDialog(
            route, self.route_service, self.route_plan_service, parent=self,
            progress_service=self.progress_service,
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
        dlg.exec()
        self.refresh()

    def _edit(self, route) -> None:
        dlg = EditLearningRouteDialog(route, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dlg.result_payload()
        try:
            self.route_service.update_route_info(route.id, **payload)
        except RouteValidationError as e:  # noqa: BLE001
            show_warning(self, str(e))
        self.refresh()

    def _pause(self, route) -> None:
        self.route_service.pause_planning(route.id)
        self.refresh()

    def _resume(self, route) -> None:
        try:
            self.route_service.resume_planning(route.id)
        except RouteValidationError as e:  # noqa: BLE001
            show_warning(self, str(e))
            return
        self.refresh()

    def _archive(self, route) -> None:
        if not self._confirm_archive_dialog():
            return
        try:
            self.route_service.archive_route(route.id)
        except RouteValidationError as e:  # noqa: BLE001
            show_warning(self, str(e))
        self.refresh()

    def _confirm_archive_dialog(self) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("归档路线")
        box.setText(
            "归档后该路线不再参与新的学习规划，历史学习记录不会删除。"
        )
        yes = box.addButton("归档", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def _restore(self, route) -> None:
        self.route_service.restore_route(route.id)
        self.refresh()
