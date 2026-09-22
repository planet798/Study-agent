"""学习路线页面（Phase C）。

展示路线树（父 group / 子 learning），支持：
- 手动创建路线 / 分组；
- 调整名称 / 目标 / 优先级；
- 暂停 / 恢复自动规划；
- 归档 / 恢复（分组有未归档子路线时拒绝归档）；
- 查看路线详情：手动创建 plan / phase / topic，安全删除 topic。

不含多路线 Scheduler / AI 生成路线（Phase D～F）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
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
    priority_stars,
    priority_text,
)
from .styles import apply_secondary_button_text


def _secondary(text: str, on_click) -> QPushButton:
    btn = QPushButton(text)
    btn.setObjectName("SecondaryButton")
    apply_secondary_button_text(btn)
    btn.clicked.connect(on_click)
    return btn


class RouteDetailDialog(QDialog):
    """路线详情：信息 + 手动学习结构（Plan/Phase/Topic）。"""

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
        self._ai_worker = None
        self.setWindowTitle(f"路线：{route.name}")
        self.setModal(True)
        self.resize(640, 620)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        self.title_label = QLabel(route.name)
        self.title_label.setObjectName("AppTitle")
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
        self.body_layout.setContentsMargins(0, 0, 6, 0)
        self.body_layout.setSpacing(6)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, stretch=1)

        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(_secondary("关闭", self.accept))
        root.addLayout(btns)

        self.refresh()

    # ---------- 渲染 ----------

    def refresh(self) -> None:
        route = self.route_service.get(self.route.id) or self.route
        self.route = route
        planning = "已启用" if route.planning_enabled else "暂停"
        status = "已归档" if route.is_archived else "进行中"
        self.info_label.setText(
            f"目标：{route.goal or '—'}\n"
            f"描述：{route.description or '—'}\n"
            f"优先级：{priority_text(route.priority)}\n"
            f"自动规划：{planning}　状态：{status}"
        )
        self.title_label.setText(route.name)

        # 顶部动作
        while self.action_row.count():
            item = self.action_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if not route.is_archived:
            self.action_row.addWidget(
                _secondary("＋ 添加阶段", self._on_add_phase)
            )
            self.action_row.addWidget(
                _secondary("AI 生成学习计划", self._on_ai_generate)
            )
            if route.planning_enabled:
                self.action_row.addWidget(
                    _secondary("暂停自动规划", self._on_pause)
                )
            else:
                self.action_row.addWidget(
                    _secondary("恢复自动规划", self._on_resume)
                )
            self.action_row.addWidget(_secondary("归档路线", self._on_archive))
        else:
            self.action_row.addWidget(_secondary("恢复路线", self._on_restore))
        self.action_row.addStretch()

        # 结构
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        structure = self.route_plan_service.get_structure(route.id)
        if structure is None:
            hint = QLabel("该路线还没有学习计划")
            hint.setObjectName("EmptyHint")
            self.body_layout.addWidget(hint)
            if not route.is_archived:
                row = QHBoxLayout()
                row.addWidget(
                    _secondary("创建手动学习计划", self._on_create_plan)
                )
                row.addWidget(
                    _secondary("AI 生成学习计划", self._on_ai_generate)
                )
                row.addStretch()
                self.body_layout.addLayout(row)
            self.body_layout.addStretch()
            return

        progress = self.route_plan_service.route_progress(route.id)
        today = self.today_provider()
        rp = None
        if self.progress_service is not None:
            try:
                rp = self.progress_service.get_progress(route.id, today)
            except Exception:  # noqa: BLE001
                rp = None
        if rp is not None:
            prog = QLabel(
                f"【路线进度】课程覆盖：{rp.topic_covered} / {rp.topic_total} Topic"
                f"　已验收：{rp.assessment_evidence_count}"
                f"　已掌握：{rp.mastered_count}"
                f"　待复习：{rp.due_review_count}"
                f"　薄弱：{rp.weak_count}"
            )
            mastery_txt = (
                "暂无验收数据" if not rp.has_assessment
                else f"掌握率 {rp.mastery_percent}%"
            )
            prog2 = QLabel(f"【掌握】{mastery_txt}")
        else:
            prog = QLabel(
                f"已完成 Topic：{progress['done']} / {progress['total']}"
                f"　阶段数：{progress['phases']}"
            )
            prog2 = None
        prog.setObjectName("TaskMeta")
        self.body_layout.addWidget(prog)
        if prog2 is not None:
            prog2.setObjectName("TaskMeta")
            self.body_layout.addWidget(prog2)

        if rp is not None and rp.activity_required_total > 0:
            act = QLabel(
                "【学习活动】必需完成："
                f"{rp.activity_required_done} / {rp.activity_required_total}"
                + (f"　可选：{rp.activity_optional_done} / {rp.activity_optional_total}"
                   if rp.activity_optional_total else "")
            )
            act.setObjectName("TaskMeta")
            self.body_layout.addWidget(act)

        if rp is not None and rp.knowledge:
            self.body_layout.addWidget(self._section_label("知识掌握 / 能力"))
            for ks in rp.knowledge:
                if ks.status == "已掌握":
                    extra = f"　{ks.mastery_percent}%"
                elif ks.status == "薄弱":
                    extra = f"　{ks.mastery_percent}%" if ks.mastery_percent is not None else ""
                    if ks.weak_points:
                        extra += f"　弱点：{'、'.join(ks.weak_points[:3])}"
                elif ks.status == "已验收":
                    extra = f"　{ks.mastery_percent}%" if ks.mastery_percent is not None else ""
                else:
                    extra = ""
                row = QHBoxLayout()
                mastery_txt = (
                    "暂无验收" if ks.mastery is None
                    else f"{ks.mastery_percent}%"
                )
                cap_txt = (
                    ks.capability_label if ks.has_capability_evidence
                    else "暂无能力证据"
                )
                lbl = QLabel(
                    f"{ks.name}　{ks.status}{extra}　Mastery：{mastery_txt}"
                    f"　Capability：{cap_txt}"
                )
                lbl.setObjectName("TaskMeta")
                lbl.setWordWrap(True)
                row.addWidget(lbl, stretch=1)
                if self.capability_service is not None and ks.knowledge_point_id:
                    row.addWidget(_secondary(
                        "查看证据",
                        lambda _=False, k=ks: self._on_view_evidence(k),
                    ))
                    if self._needs_experiment_record(ks):
                        row.addWidget(_secondary(
                            "记录实验成果",
                            lambda _=False, k=ks: self._on_record_experiment(k),
                        ))
                self.body_layout.addLayout(row)
            self.body_layout.addWidget(self._section_label("复习状态"))
            last_assessed = max(
                (k.last_assessed_at for k in rp.knowledge if k.last_assessed_at),
                default=None,
            )
            rev = QLabel(
                f"今日到期：{rp.due_review_count}　未来7天：{rp.upcoming_review_count}"
                f"　逾期：{rp.overdue_review_count}"
                f"　最近复习：{last_assessed or '—'}"
            )
            rev.setObjectName("TaskMeta")
            rev.setWordWrap(True)
            self.body_layout.addWidget(rev)

        done_ids = self._complete_topic_ids()
        if not structure.phases:
            self.body_layout.addWidget(
                _secondary("＋ 添加阶段", self._on_add_phase)
            )
        for phase in structure.phases:
            self.body_layout.addWidget(self._phase_card(phase, done_ids))
        self._add_skills_section()
        self._add_curriculum_gap_section()
        self._add_linked_projects_section()
        self._add_practice_blockers_section()
        self.body_layout.addStretch()

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("SectionTitle")
        return lbl

    def _add_linked_projects_section(self) -> None:
        """Phase 4：轻量展示关联实践项目（不把完整项目管理塞进 RouteDetail）。"""
        if self.practice_service is None:
            return
        try:
            projects = self.practice_service.list_by_route(self.route.id)
        except Exception:  # noqa: BLE001
            return
        self.body_layout.addWidget(self._section_label("关联实践项目"))
        if not projects:
            empty = QLabel("关联实践项目：0")
            empty.setObjectName("TaskMeta")
            self.body_layout.addWidget(empty)
            return
        for p in projects:
            lbl = QLabel(f"关联实践项目：{p['name']}")
            lbl.setObjectName("TaskMeta")
            self.body_layout.addWidget(lbl)

    def _add_practice_blockers_section(self) -> None:
        """Phase 6：轻量展示该路线的项目学习阻塞（不展开完整 Project Dashboard）。"""
        if self.practice_readiness_service is None:
            return
        try:
            blockers = self.practice_readiness_service.list_route_blockers(
                self.route.id, self.today_provider()
            )
        except Exception:  # noqa: BLE001
            return
        self.body_layout.addWidget(self._section_label("项目学习阻塞"))
        if not blockers:
            empty = QLabel("项目学习阻塞：0")
            empty.setObjectName("TaskMeta")
            self.body_layout.addWidget(empty)
            return
        hdr = QLabel(f"项目学习阻塞：{len(blockers)}")
        hdr.setObjectName("TaskMeta")
        self.body_layout.addWidget(hdr)
        for st in blockers:
            nxt = st.next_activity_label or st.reason_label
            lbl = QLabel(
                f"{st.topic_name}　{st.current_capability_label} → "
                f"{st.target_capability_label}　下一步：{nxt}"
            )
            lbl.setObjectName("TaskMeta")
            lbl.setWordWrap(True)
            self.body_layout.addWidget(lbl)

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
        card = QFrame()
        card.setObjectName("TaskCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(4)

        head = QHBoxLayout()
        name = QLabel(f"阶段 {phase.order_index or '—'}：{phase.name}")
        name.setObjectName("TaskTitle")
        head.addWidget(name)
        head.addStretch()
        if not self.route.is_archived:
            head.addWidget(_secondary(
                "＋ 添加知识点",
                lambda _=False, p=phase: self._on_add_topic(p),
            ))
            head.addWidget(_secondary(
                "删除阶段", lambda _=False, p=phase: self._on_delete_phase(p)
            ))
        lay.addLayout(head)

        if phase.goals:
            goal = QLabel(f"目标：{phase.goals}")
            goal.setObjectName("TaskMeta")
            goal.setWordWrap(True)
            lay.addWidget(goal)

        if not phase.topics:
            empty = QLabel("暂无知识点")
            empty.setObjectName("TaskMeta")
            lay.addWidget(empty)
        for topic in phase.topics:
            row = QHBoxLayout()
            mark = "✔ " if topic.id in done_ids else ""
            text = f"{mark}{topic.name}（预计 {topic.estimated_minutes} 分钟）"
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            row.addWidget(lbl, stretch=1)
            # Phase 2：学习活动 chips
            if self.topic_learning_service is not None:
                chips = self._activity_chips(topic.id)
                if chips:
                    chip_lbl = QLabel(chips)
                    chip_lbl.setObjectName("TaskMeta")
                    row.addWidget(chip_lbl)
                    row.addWidget(_secondary(
                        "学习组成",
                        lambda _=False, t=topic: self._on_edit_profile(t),
                    ))
            if not self.route.is_archived:
                row.addWidget(_secondary(
                    "删除", lambda _=False, t=topic: self._on_delete_topic(t)
                ))
            lay.addLayout(row)
        return card

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
            return ""
        parts = []
        for s in statuses:
            if s["complete"]:
                mark = "✓"
            elif s["required"]:
                mark = "○"
            else:
                mark = "◇"
            parts.append(f"{s['label']} {mark}")
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
        self.body_layout.addWidget(self._section_label("关联技能"))
        route_repo = self.route_service.route_repo
        try:
            skill_ids = route_repo.list_skill_ids(self.route.id)
        except Exception:  # noqa: BLE001
            skill_ids = []
        skills = []
        for sid in skill_ids:
            s = self.skill_service.skill_repo.get(sid)
            if s is not None:
                skills.append(s)
        if not skills:
            empty = QLabel("尚未关联学习路线")
            empty.setObjectName("TaskMeta")
            self.body_layout.addWidget(empty)
        for s in skills:
            row = QHBoxLayout()
            lbl = QLabel(f"{s['name']}（{s.get('tier', '')}级）")
            row.addWidget(lbl, 1)
            if not self.route.is_archived:
                row.addWidget(_secondary(
                    "移除关联", lambda _=False, sk=s: self._on_remove_skill(sk)
                ))
            self.body_layout.addLayout(row)
        if not self.route.is_archived:
            self.body_layout.addWidget(_secondary(
                "＋ 关联已有技能", self._on_assign_skill
            ))

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
            gaps = self.skill_service.curriculum_gap_skills(
                route_id=self.route.id
            )
        except Exception:  # noqa: BLE001
            gaps = []
        self.body_layout.addWidget(self._section_label("课程缺口"))
        if not gaps:
            empty = QLabel("暂无课程缺口")
            empty.setObjectName("TaskMeta")
            self.body_layout.addWidget(empty)
            return
        for g in gaps:
            row = QHBoxLayout()
            freq = float(g.get("frequency_30d") or 0.0) * 100
            lbl = QLabel(
                f"{g['skill']}　近30天目标岗位需求：{freq:.1f}%　当前路线暂无 Topic"
            )
            lbl.setWordWrap(True)
            row.addWidget(lbl, 1)
            if not self.route.is_archived:
                row.addWidget(_secondary(
                    "添加知识点",
                    lambda _=False, name=g["skill"]: self._on_gap_add_topic(name),
                ))
            self.body_layout.addLayout(row)

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
        worker.succeeded.connect(self._on_draft_ready)
        worker.failed.connect(self._on_draft_failed)
        self._ai_worker = worker
        worker.start()

    def _on_draft_failed(self, message: str) -> None:
        show_warning(self, f"AI 生成失败：{message}")

    def _on_draft_ready(self, draft) -> None:
        mode, reason = self.route_plan_service.ai_plan_mode(self.route.id)
        if mode == "blocked":
            show_warning(self, reason)
            return
        preview = RouteDraftPreviewDialog(draft, mode=mode, parent=self)
        if preview.exec() != QDialog.DialogCode.Accepted:
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
        root.setSpacing(8)

        head = QHBoxLayout()
        head.addStretch()
        self.create_btn = _secondary("＋ 新建学习路线", self._on_create_route)
        self.archive_toggle_btn = _secondary("查看已归档", self._on_toggle_archived)
        head.addWidget(self.create_btn)
        head.addWidget(self.archive_toggle_btn)
        root.addLayout(head)

        hint = QLabel(
            "路线用于组织学习内容；优先级与自动规划将在后续阶段参与每日调度。"
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
        self.list_layout.setSpacing(6)
        self.scroll.setWidget(self.list_container)
        root.addWidget(self.scroll, stretch=1)

    # ---------- 渲染 ----------

    def refresh(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        roots = self.route_service.route_repo.list_roots()
        active_roots = [r for r in roots if not r.is_archived]
        archived_any = []

        for root in active_roots:
            if root.route_type == ROUTE_TYPE_GROUP:
                children = self.route_service.route_repo.list_children(root.id)
                active_children = [c for c in children if not c.is_archived]
                self.list_layout.addWidget(self._group_card(root, active_children))
                for child in active_children:
                    self.list_layout.addWidget(self._route_card(child))
                archived_any += [c for c in children if c.is_archived]
            else:
                self.list_layout.addWidget(self._route_card(root))

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

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("SectionTitle")
        return lbl

    def _group_card(self, route, active_children) -> QWidget:
        card = QFrame()
        card.setObjectName("TaskCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(3)
        name = QLabel(route.name)
        name.setObjectName("TaskTitle")
        lay.addWidget(name)
        if self.progress_service is not None:
            try:
                gs = self.progress_service.group_summary(route.id)
            except Exception:  # noqa: BLE001
                gs = None
        else:
            gs = None
        if gs is not None:
            meta_text = (
                f"类型：分组　子路线：{gs['children_total']}（活跃 {gs['active']} /"
                f" 暂停 {gs['paused']} / 已归档 {gs['archived']}）"
                f"　优先级：{priority_text(route.priority)}"
            )
        else:
            meta_text = (
                f"类型：分组　子路线：{len(active_children)}　"
                f"优先级：{priority_text(route.priority)}"
            )
        meta = QLabel(meta_text)
        meta.setObjectName("TaskMeta")
        lay.addWidget(meta)
        if route.goal:
            goal = QLabel(f"目标：{route.goal}")
            goal.setObjectName("TaskMeta")
            goal.setWordWrap(True)
            lay.addWidget(goal)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(_secondary(
            "归档分组", lambda _=False, r=route: self._archive(r)
        ))
        lay.addLayout(row)
        return card

    def _route_card(self, route) -> QWidget:
        card = QFrame()
        card.setObjectName("TaskCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(3)

        name = QLabel(route.name)
        name.setObjectName("TaskTitle")
        lay.addWidget(name)

        planning = "已启用" if route.planning_enabled else "暂停"
        current = self._current_phase_name(route.id)
        progress = (
            self.route_plan_service.route_progress(route.id)
            if self.route_plan_service is not None
            else {"done": 0, "total": 0}
        )
        status = "已归档" if route.is_archived else "进行中"
        meta = QLabel(
            f"优先级：{priority_stars(route.priority)}　状态：{status}　"
            f"自动规划：{planning}\n"
            f"当前阶段：{current}　已完成 Topic："
            f"{progress['done']} / {progress['total']}"
        )
        meta.setObjectName("TaskMeta")
        lay.addWidget(meta)
        if route.goal:
            goal = QLabel(f"目标：{route.goal}")
            goal.setObjectName("TaskMeta")
            goal.setWordWrap(True)
            lay.addWidget(goal)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(_secondary(
            "查看路线", lambda _=False, r=route: self._open_detail(r)
        ))
        if not route.is_archived:
            row.addWidget(_secondary(
                "调整", lambda _=False, r=route: self._edit(r)
            ))
            if route.planning_enabled:
                row.addWidget(_secondary(
                    "暂停自动规划", lambda _=False, r=route: self._pause(r)
                ))
            else:
                row.addWidget(_secondary(
                    "恢复自动规划", lambda _=False, r=route: self._resume(r)
                ))
            row.addWidget(_secondary(
                "归档", lambda _=False, r=route: self._archive(r)
            ))
        else:
            row.addWidget(_secondary(
                "恢复路线", lambda _=False, r=route: self._restore(r)
            ))
        lay.addLayout(row)
        return card

    def _current_phase_name(self, route_id: int) -> str:
        if self.route_plan_service is None:
            return "—"
        structure = self.route_plan_service.get_structure(route_id)
        if structure is None or not structure.phases:
            return "尚未创建学习计划"
        done_ids = self._complete_topic_ids()
        for phase in structure.phases:
            if any(t.id not in done_ids for t in phase.topics):
                return phase.name
        return structure.phases[-1].name

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
