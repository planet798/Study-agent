"""实践项目页面与详情（Phase 4）。

独立一级页面；Practice 不放进学习路线树。
Project Detail 更新 milestone/output 后保持自身滚动位置。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
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

from ..services.practice import (
    STATUS_ARCHIVED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_PLANNED,
    milestone_status_label,
    output_type_label,
    project_status_label,
    project_type_label,
)
from ..services.practice_project_service import PracticeError
from .practice_dialogs import (
    CreatePracticeProjectDialog,
    EditPracticeProjectDialog,
    ManageProjectRoutesDialog,
    ManageProjectSkillsDialog,
    ManageProjectTopicsDialog,
    MilestoneEditorDialog,
    OutputEditorDialog,
    PracticeTopicEvidenceDialog,
    PracticeTopicRequirementDialog,
    RevokeEvidenceDialog,
)
from .components.button import SAButton
from .components.card import SACard
from .components.empty_state import SAEmptyState
from .components.progress_bar import SAProgressBar
from .components.section_header import SASectionHeader
from .components.status_badge import SAStatusBadge
from .components.tag import SATag
from .design import icons as _icons
from .design import spacing as _spacing
from .components.scroll_position import ScrollPositionKeeper
from .components.workspace_sections import plain_label
from .practice_overview_widgets import PracticeOverviewRow, ProjectMetrics


def _project_metrics(service, capability_service, readiness_service, project_id):
    """服务读取的可展示快照；未知值不可伪装为零。"""
    try:
        progress = service.get_project_progress(project_id)
        milestones = (f"{progress['milestones_done']} / {progress['milestones_total']}"
                      if progress['has_milestones'] else "尚未设置里程碑")
        outputs = str(progress['output_count'])
    except Exception:
        milestones = outputs = "暂不可用"
    evidence = "未启用"
    if capability_service is not None:
        try:
            evidence = str(capability_service.count_active_by_project(project_id))
        except Exception:
            evidence = "暂不可用"
    readiness = "未启用"
    if readiness_service is not None:
        try:
            data = readiness_service.get_project_readiness(project_id)
            readiness = f"{data['satisfied_count']} / {data['total_count']} 已满足"
        except Exception:
            readiness = "暂不可用"
    return ProjectMetrics(milestones, outputs, evidence, readiness)



def _project_status_key(status: str) -> str:
    if status == STATUS_ARCHIVED:
        return "archived"
    if status == STATUS_COMPLETED:
        return "completed"
    if status == STATUS_IN_PROGRESS:
        return "in_progress"
    return "planned"


def _secondary(text: str, slot) -> QPushButton:
    btn = SAButton(text, variant="secondary", size="small")
    btn.clicked.connect(slot)
    return btn


def _clear_layout(layout) -> None:
    """递归清空 layout（含嵌套 QHBoxLayout 行）。"""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            _clear_layout(child)


_FILTERS = (
    ("全部未归档", None),
    ("进行中", STATUS_IN_PROGRESS),
    ("计划中", STATUS_PLANNED),
    ("已完成", STATUS_COMPLETED),
    ("已归档", STATUS_ARCHIVED),
)


class PracticeProjectsPage(QWidget):
    def __init__(self, practice_service, route_repo, skill_repo, plan_repo,
                 capability_service=None, parent=None,
                 readiness_service=None):
        super().__init__(parent)
        self.service = practice_service
        self.route_repo = route_repo
        self.skill_repo = skill_repo
        self.plan_repo = plan_repo
        self.capability_service = capability_service
        self.readiness_service = readiness_service
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(QLabel("状态"))
        self.filter_combo = QComboBox()
        for label, value in _FILTERS:
            self.filter_combo.addItem(label, value)
        self.filter_combo.currentIndexChanged.connect(lambda *_: self.refresh(reset_scroll=True))
        head.addWidget(self.filter_combo)
        head.addStretch()
        self.create_btn = SAButton(
            "新建项目", variant="secondary", icon_name=_icons.IconName.ADD
        )
        self.create_btn.clicked.connect(self._on_create)
        head.addWidget(self.create_btn)
        root.addLayout(head)

        hint = plain_label("用里程碑推进项目，用成果与确认的证据记录实践能力。")
        root.addWidget(hint)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(_spacing.SM)
        self.scroll.setWidget(self.list_container)
        self._scroll_keeper = ScrollPositionKeeper(self.scroll)
        self.empty_action_btn = SAButton(
            "新建项目", variant="secondary", icon_name=_icons.IconName.ADD
        )
        self._empty_action = "create"
        self.empty_action_btn.clicked.connect(self._on_empty_action)
        self.empty_state = SAEmptyState(
            title="还没有实践项目",
            description="创建项目，把学习路线、技能和 Topic 转化为可验证成果。",
            icon_name=_icons.IconName.PROJECT,
            action=self.empty_action_btn,
        )
        self.empty_state.setVisible(False)
        root.addWidget(self.empty_state)
        root.addWidget(self.scroll, stretch=1)

    # ---------- 渲染 ----------

    def refresh(self, *, reset_scroll=False) -> None:
        value = self._scroll_keeper.capture()
        _clear_layout(self.list_layout)
        self.empty_state.setVisible(False)
        self.scroll.setVisible(True)
        self.create_btn.setVisible(True)
        status = self.filter_combo.currentData()
        try:
            projects = (self.service.projects.list_active() if status is None
                        else self.service.list_projects(status=status))
            all_projects = self.service.list_projects() if not projects else None
        except Exception:
            self._show_empty("项目数据暂不可用", "稍后重试，当前筛选保持不变。", "retry", "重试")
            return
        if not projects:
            if not all_projects:
                self._show_empty("还没有实践项目", "新建项目，安排里程碑并记录实践成果。", "create", "新建项目")
                self.create_btn.hide()
            elif status is None:
                self._show_empty("项目已全部归档", "可查看归档记录，或从上方新建项目。", "archived", "查看已归档")
            else:
                self._show_empty("当前筛选下没有项目", "切换到全部未归档项目继续浏览。", "clear", "清除筛选")
            return
        for project in projects:
            self.list_layout.addWidget(self._project_card(project))
        self.list_layout.addStretch()
        self.list_layout.activate()
        self._scroll_keeper.restore(0 if reset_scroll else value)

    def _show_empty(self, title, description, action, action_text):
        self._empty_action = action
        self.empty_state.set_title(title)
        self.empty_state.set_description(description)
        self.empty_action_btn.setText(action_text)
        self.empty_state.show()
        self.scroll.hide()

    def _on_empty_action(self):
        if self._empty_action == "create":
            self._on_create()
        elif self._empty_action == "archived":
            self.filter_combo.setCurrentIndex(self.filter_combo.findData(STATUS_ARCHIVED))
        elif self._empty_action == "clear":
            self.filter_combo.setCurrentIndex(0)
        else:
            self.refresh()

    def _project_card(self, project: dict) -> QWidget:
        row = PracticeOverviewRow(
            name=project['name'], status=_project_status_key(project['status']),
            project_type=project_type_label(project['project_type']),
            routes=self._project_route_names(project['id']),
            metrics=_project_metrics(self.service, self.capability_service,
                                     self.readiness_service, project['id']),
        )
        row.setProperty("projectId", project['id'])
        handlers = {"open": self._open_detail, "edit": self._edit,
                    "archive": self._archive, "restore": self._restore}
        row.requested.connect(lambda key, p=project: handlers[key](p))
        return row

    @staticmethod
    def _meta(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("TaskMeta")
        lbl.setWordWrap(True)
        return lbl

    def _project_route_names(self, project_id) -> list[str]:
        if self.route_repo is None:
            return []
        try:
            names = []
            for rid in self.service.projects.list_route_ids(project_id):
                route = self.route_repo.get(rid)
                if route is not None:
                    names.append(route.name)
            return names
        except Exception:
            return ["暂不可用"]

    # ---------- 操作 ----------

    def _on_create(self) -> None:
        routes = []
        if self.route_repo is not None:
            routes = [
                r for r in self.route_repo.list_learning_routes()
                if r.status != STATUS_ARCHIVED
            ]
        dlg = CreatePracticeProjectDialog(routes, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dlg.result_payload()
        try:
            self.service.create_project(**payload)
        except (PracticeError, ValueError) as e:
            QMessageBox.warning(self, "创建失败", str(e))
            return
        self.refresh()

    def _open_detail(self, project: dict) -> None:
        dlg = PracticeProjectDetailDialog(
            project["id"], self.service, self.route_repo, self.skill_repo,
            self.plan_repo, self.capability_service, parent=self,
            readiness_service=self.readiness_service,
        )
        dlg.exec()
        self.refresh()

    def _edit(self, project: dict) -> None:
        dlg = EditPracticeProjectDialog(project, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.service.update_project(project["id"], **dlg.result_payload())
        except (PracticeError, ValueError) as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
        self.refresh()

    def _archive(self, project: dict) -> None:
        if QMessageBox.question(
            self, "归档项目",
            f"确定归档「{project['name']}」吗？不会删除里程碑/成果/关联。",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.service.archive_project(project["id"])
        self.refresh()

    def _restore(self, project: dict) -> None:
        self.service.restore_project(project["id"])
        self.refresh()


class PracticeProjectDetailDialog(QDialog):
    def __init__(self, project_id, service, route_repo, skill_repo, plan_repo,
                 capability_service=None, parent=None,
                 readiness_service=None):
        super().__init__(parent)
        self.project_id = project_id
        self.service = service
        self.route_repo = route_repo
        self.skill_repo = skill_repo
        self.plan_repo = plan_repo
        self.capability_service = capability_service
        self.readiness_service = readiness_service
        self._scroll_restore_epoch = 0
        self._scroll_restore_handler = None
        self._closing = False
        self.setWindowTitle("实践项目")
        self.setModal(True)
        self.resize(680, 640)
        root = QVBoxLayout(self)
        self.title_label = QLabel("")
        self.title_label.setObjectName("AppTitle")
        root.addWidget(self.title_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 6, 0)
        self.body_layout.setSpacing(6)
        self.scroll.setWidget(self.body)
        self._scroll_viewport = self.scroll.viewport()
        self._scroll_bar = self.scroll.verticalScrollBar()
        self._scroll_viewport.installEventFilter(self)
        self._scroll_bar.installEventFilter(self)
        self._scroll_bar.sliderPressed.connect(self._clear_scroll_restore)
        root.addWidget(self.scroll, stretch=1)

        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(_secondary("关闭", self.accept))
        root.addLayout(btns)
        self.refresh()

    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("SectionTitle")
        return lbl

    @staticmethod
    def _value_pair(lay, label: str, value: str) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        key = QLabel(label)
        key.setObjectName("SAValueLabel")
        val = QLabel(value if value else "—")
        val.setObjectName("SAValueStrong")
        val.setWordWrap(True)
        row.addWidget(key)
        row.addWidget(val, stretch=1)
        lay.addLayout(row)

    def refresh(self) -> None:
        value = self._scroll_bar.value()
        self._clear_scroll_restore()
        _clear_layout(self.body_layout)
        detail = self.service.get_project_detail(self.project_id)
        p = detail["project"]
        self.title_label.setText(p["name"])
        prog = self.service.get_project_progress(self.project_id)
        ms_txt = (f"{prog['milestones_done']} / {prog['milestones_total']}"
                  if prog["has_milestones"] else "尚未设置里程碑")
        card = SACard()
        card.add_widget(SASectionHeader("概览"))
        lay = card.body_layout
        self._value_pair(lay, "状态", project_status_label(p['status']))
        self._value_pair(lay, "类型", project_type_label(p['project_type']))
        self._value_pair(lay, "目标", p.get('goal') or '—')
        self._value_pair(lay, "描述", p.get('description') or '—')
        self._value_pair(lay, "里程碑", ms_txt)
        self._value_pair(lay, "成果", str(prog['output_count']))
        self.body_layout.addWidget(card)

        scope_card = SACard()
        scope_card.add_widget(SASectionHeader("项目范围"))
        scope_lay = scope_card.body_layout
        self.body_layout.addWidget(scope_card)
        self._routes_section(detail["routes"], lay=scope_lay)
        self._skills_section(detail["skills"], lay=scope_lay)
        self._topics_section(detail["topics"], lay=scope_lay)
        self._readiness_section()
        self._milestones_section(detail["milestones"])
        self._outputs_section(detail["outputs"])
        self._evidence_section()
        self.body_layout.addStretch()
        self._restore_scroll(value)

    def _clear_scroll_restore(self) -> None:
        """Invalidate queued restores and disconnect the only range handler."""
        self._scroll_restore_epoch += 1
        handler = self._scroll_restore_handler
        self._scroll_restore_handler = None
        if handler is not None:
            try:
                self._scroll_bar.rangeChanged.disconnect(handler)
            except (RuntimeError, TypeError):
                pass

    def _restore_scroll(self, value: int) -> None:
        self._clear_scroll_restore()
        if self._closing:
            return
        target = max(0, int(value))
        if target == 0:
            self._scroll_bar.setValue(0)
            return
        epoch = self._scroll_restore_epoch
        bar = self._scroll_bar

        def _apply(*_args) -> None:
            if self._closing or epoch != self._scroll_restore_epoch:
                return
            if bar.maximum() <= 0:
                return  # wait for a real range/layout update
            bar.setValue(min(target, bar.maximum()))
            self._clear_scroll_restore()

        self._scroll_restore_handler = _apply
        bar.rangeChanged.connect(_apply)
        QTimer.singleShot(0, self, _apply)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if (watched is getattr(self, "_scroll_viewport", None)
                or watched is getattr(self, "_scroll_bar", None)) and event.type() in (
                    QEvent.Type.Wheel, QEvent.Type.MouseButtonPress,
                    QEvent.Type.TouchBegin,
                ):
            self._clear_scroll_restore()
        return super().eventFilter(watched, event)

    def _finish_scroll_lifecycle(self) -> None:
        self._closing = True
        self._clear_scroll_restore()

    def done(self, result):  # noqa: N802 - accept/reject can hide the dialog
        self._finish_scroll_lifecycle()
        super().done(result)

    def closeEvent(self, event):  # noqa: N802 - Qt API
        self._finish_scroll_lifecycle()
        super().closeEvent(event)

    def showEvent(self, event):  # noqa: N802 - a hidden dialog may reopen
        self._closing = False
        super().showEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.DeferredDelete:
            self._finish_scroll_lifecycle()
        return super().event(event)

    def _routes_section(self, routes, lay=None) -> None:
        if lay is None:
            lay = self.body_layout
        head = QHBoxLayout()
        head.addWidget(self._section("关联学习路线"))
        head.addStretch()
        head.addWidget(_secondary("管理路线", self._manage_routes))
        lay.addLayout(head)
        names = " · ".join(r.name for r in routes) if routes else "—"
        lbl = QLabel(names)
        lbl.setObjectName("TaskMeta")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)

    def _skills_section(self, skills, lay=None) -> None:
        if lay is None:
            lay = self.body_layout
        head = QHBoxLayout()
        head.addWidget(self._section("关联技能"))
        head.addStretch()
        head.addWidget(_secondary("管理技能", self._manage_skills))
        lay.addLayout(head)
        names = "、".join(s["name"] for s in skills) if skills else "—"
        lbl = QLabel(names)
        lbl.setObjectName("TaskMeta")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)

    def _topics_section(self, topics, lay=None) -> None:
        if lay is None:
            lay = self.body_layout
        head = QHBoxLayout()
        head.addWidget(self._section("关联 Topic"))
        head.addStretch()
        head.addWidget(_secondary("管理 Topic", self._manage_topics))
        lay.addLayout(head)
        names = "、".join(t["name"] for t in topics) if topics else "—"
        lbl = QLabel(names)
        lbl.setObjectName("TaskMeta")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)

    def _milestones_section(self, milestones) -> None:
        card = SACard()
        card.add_widget(SASectionHeader("里程碑"))
        lay = card.body_layout
        self.body_layout.addWidget(card)
        head = QHBoxLayout()
        head.addStretch()
        head.addWidget(_secondary("新增里程碑", self._add_milestone))
        lay.addLayout(head)
        if not milestones:
            lbl = QLabel("尚未设置里程碑")
            lbl.setObjectName("TaskMeta")
            lay.addWidget(lbl)
            return
        for m in milestones:
            row = QHBoxLayout()
            lbl = QLabel(
                f"#{m['order_index']} {m['title']}　"
                f"[{milestone_status_label(m['status'])}]"
            )
            lbl.setObjectName("TaskMeta")
            lbl.setWordWrap(True)
            row.addWidget(lbl, stretch=1)
            nxt = {"todo": "in_progress", "in_progress": "done",
                   "done": "todo"}[m["status"]]
            row.addWidget(_secondary(
                "推进", lambda _=False, mm=m, n=nxt: self._advance_milestone(mm, n)
            ))
            row.addWidget(_secondary(
                "编辑", lambda _=False, mm=m: self._edit_milestone(mm)
            ))
            row.addWidget(_secondary(
                "删除", lambda _=False, mm=m: self._delete_milestone(mm)
            ))
            lay.addLayout(row)

    def _outputs_section(self, outputs) -> None:
        card = SACard()
        card.add_widget(SASectionHeader("项目成果"))
        lay = card.body_layout
        self.body_layout.addWidget(card)
        head = QHBoxLayout()
        head.addStretch()
        head.addWidget(_secondary("新增成果", self._add_output))
        lay.addLayout(head)
        if not outputs:
            lbl = QLabel("暂无项目成果")
            lbl.setObjectName("TaskMeta")
            lay.addWidget(lbl)
            return
        for o in outputs:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(
                SATag(output_type_label(o['output_type']), "neutral")
            )
            uri = f"　{o['uri']}" if o.get("uri") else ""
            lbl = QLabel(f"{o['title']}{uri}")
            lbl.setObjectName("TaskMeta")
            lbl.setWordWrap(True)
            row.addWidget(lbl, stretch=1)
            row.addWidget(_secondary(
                "编辑", lambda _=False, oo=o: self._edit_output(oo)
            ))
            row.addWidget(_secondary(
                "删除", lambda _=False, oo=o: self._delete_output(oo)
            ))
            lay.addLayout(row)

    # ---------- Phase 6：学习准备度 ----------

    def _readiness_section(self) -> None:
        card = SACard()
        card.add_widget(SASectionHeader("学习准备"))
        lay = card.body_layout
        self.body_layout.addWidget(card)
        head = QHBoxLayout()
        head.addStretch()
        lay.addLayout(head)
        if self.readiness_service is None:
            lbl = QLabel("未启用学习准备度")
            lbl.setObjectName("TaskMeta")
            lay.addWidget(lbl)
            return
        try:
            readiness = self.readiness_service.get_project_readiness(
                self.project_id
            )
        except Exception as e:  # noqa: BLE001
            lbl = QLabel(f"学习准备度不可用：{e}")
            lbl.setObjectName("TaskMeta")
            lay.addWidget(lbl)
            return
        summary = QLabel(
            f"学习准备度：{readiness['satisfied_count']} / "
            f"{readiness['total_count']} 已满足"
            + (f"　待补 {readiness['pending_count']} 项"
               if readiness['pending_count'] else "")
            + ("　（项目未在进行中：只展示，不驱动 Planner）"
               if not readiness["drives_planner"] else "")
        )
        summary.setObjectName("TaskMeta")
        summary.setWordWrap(True)
        lay.addWidget(summary)
        statuses = readiness.get("statuses") or []
        covered = {int(st.topic_id) for st in statuses}
        for st in statuses:
            row = QHBoxLayout()
            if st.satisfied:
                state = "已满足"
            else:
                state = f"能力缺口（{st.reason_label}）"
            lines = [
                st.topic_name,
                f"要求：{st.target_capability_label}（{st.target_capability_level}）",
                f"当前：{st.current_capability_label}",
                state,
            ]
            if st.planner_actionable and st.next_activity_label:
                lines.append(f"下一步：{st.next_activity_label}")
            elif st.reason_code == "needs_assessment":
                lines.append("建议：进行验收")
            elif st.reason_code == "needs_experiment_evidence":
                lines.append("建议：记录实验成果")
            if st.note:
                lines.append(f"备注：{st.note}")
            lbl = QLabel("\n".join(lines))
            lbl.setObjectName("TaskMeta")
            lbl.setWordWrap(True)
            row.addWidget(lbl, stretch=1)
            row.addWidget(_secondary(
                "设置学习要求",
                lambda _=False, tid=st.topic_id, nm=st.topic_name:
                    self._edit_requirement(tid, nm),
            ))
            lay.addLayout(row)

        # 已关联但未设置要求的 Topic（§75：不强制，用户需要时再设）
        for tid in self.service.projects.list_topic_ids(self.project_id):
            if int(tid) in covered:
                continue
            topic = self.plan_repo.get_topic(tid)
            name = getattr(topic, "name", "") if topic else ""
            row = QHBoxLayout()
            lbl = QLabel(f"{name}\n能力要求：未设置")
            lbl.setObjectName("TaskMeta")
            lbl.setWordWrap(True)
            row.addWidget(lbl, stretch=1)
            row.addWidget(_secondary(
                "设置学习要求",
                lambda _=False, tid=tid, nm=name: self._edit_requirement(tid, nm),
            ))
            lay.addLayout(row)

    def _edit_requirement(self, topic_id, topic_name) -> None:
        project = self.service.projects.get(self.project_id)
        existing = self.readiness_service.get_requirement(
            self.project_id, topic_id
        )
        dlg = PracticeTopicRequirementDialog(
            project, topic_id, topic_name,
            self.readiness_service, existing=existing, parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    # ---------- Phase 5：项目能力证据 ----------

    def _evidence_section(self) -> None:
        card = SACard()
        card.add_widget(SASectionHeader("项目能力证据"))
        lay = card.body_layout
        self.body_layout.addWidget(card)
        head = QHBoxLayout()
        head.addStretch()
        lay.addLayout(head)
        if self.capability_service is None:
            lbl = QLabel("未启用项目能力证据")
            lbl.setObjectName("TaskMeta")
            lay.addWidget(lbl)
            return
        candidates = self.capability_service.list_evidence_candidates(
            self.project_id
        )
        if not candidates:
            lbl = QLabel(
                "项目尚未关联 Topic；关联后可逐 Topic 确认项目使用证据。"
            )
            lbl.setObjectName("TaskMeta")
            lbl.setWordWrap(True)
            lay.addWidget(lbl)
            return
        for c in candidates:
            row = QHBoxLayout()
            if c["has_active_evidence"]:
                ev = self.capability_service.get_topic_evidence(
                    c["active_evidence_id"]
                ) or {}
                outputs = " · ".join(ev.get("output_labels") or [])
                lbl = QLabel(
                    f"{c['topic_name']}\n已确认项目使用证据\n"
                    f"支撑成果：{outputs or '—'}\n"
                    f"项目使用：{ev.get('usage_description') or ''}"
                )
                row.addWidget(lbl, stretch=1)
                row.addWidget(_secondary(
                    "查看证据",
                    lambda _=False, cc=c: self._view_topic_evidence(cc),
                ))
                row.addWidget(_secondary(
                    "撤销证据",
                    lambda _=False, cc=c: self._revoke_topic_evidence(cc),
                ))
            else:
                if c["eligible"]:
                    status = "可确认项目能力证据"
                    if c.get("has_historical_evidence"):
                        status = "曾有已撤销证据（可重新确认）"
                else:
                    status = (
                        "尚未确认项目使用证据"
                        + (f"（{c['reason_label']}）" if c["reason_label"] else "")
                    )
                    if c.get("has_historical_evidence"):
                        status += "　曾有已撤销证据"
                lbl = QLabel(f"{c['topic_name']}\n{status}")
                row.addWidget(lbl, stretch=1)
                btn = _secondary(
                    "确认项目使用",
                    lambda _=False, cc=c: self._confirm_topic_evidence(cc),
                )
                btn.setEnabled(bool(c["eligible"]))
                row.addWidget(btn)
            lbl.setObjectName("TaskMeta")
            lbl.setWordWrap(True)
            lay.addLayout(row)

    def _confirm_topic_evidence(self, candidate) -> None:
        project = self.service.projects.get(self.project_id)
        outputs = self.service.outputs.list_by_project(self.project_id)
        route = None
        if candidate.get("route_id") is not None and self.route_repo is not None:
            route = self.route_repo.get(candidate["route_id"])
        dlg = PracticeTopicEvidenceDialog(
            project, candidate["topic_id"], candidate["topic_name"],
            getattr(route, "name", "") if route else "", outputs,
            self.capability_service, parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _view_topic_evidence(self, candidate) -> None:
        from .capability_dialog import CapabilityEvidenceDialog

        CapabilityEvidenceDialog(
            candidate["topic_name"], candidate["knowledge_point_id"],
            self.capability_service.capability_service,
            practice_capability_service=self.capability_service, parent=self,
        ).exec()

    def _revoke_topic_evidence(self, candidate) -> None:
        dlg = RevokeEvidenceDialog(candidate["topic_name"], parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.capability_service.revoke_project_topic_evidence(
                candidate["active_evidence_id"], dlg.reason()
            )
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "撤销失败", str(e))
            return
        self.refresh()

    # ---------- manage ----------

    def _active_routes(self):
        return [r for r in self.route_repo.list_learning_routes()
                if r.status != STATUS_ARCHIVED]

    def _project_routes(self):
        p = self.service.projects.get(self.project_id)
        out = []
        for rid in self.service.projects.list_route_ids(self.project_id):
            r = self.route_repo.get(rid)
            if r is not None:
                out.append(r)
        return out

    def _manage_routes(self) -> None:
        p = self.service.projects.get(self.project_id)
        dlg = ManageProjectRoutesDialog(p, self.service, self._active_routes(),
                                        parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _manage_skills(self) -> None:
        p = self.service.projects.get(self.project_id)
        dlg = ManageProjectSkillsDialog(
            p, self.service, self.skill_repo,
            self.service.projects.list_route_ids(self.project_id), parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _manage_topics(self) -> None:
        p = self.service.projects.get(self.project_id)
        dlg = ManageProjectTopicsDialog(
            p, self.service, self.plan_repo, self._project_routes(),
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _add_milestone(self) -> None:
        p = self.service.projects.get(self.project_id)
        dlg = MilestoneEditorDialog(self.service, p, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _edit_milestone(self, milestone) -> None:
        p = self.service.projects.get(self.project_id)
        dlg = MilestoneEditorDialog(self.service, p, milestone, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _delete_milestone(self, milestone) -> None:
        try:
            self.service.delete_milestone(milestone["id"])
        except PracticeError as e:
            QMessageBox.warning(self, "无法删除", str(e))
            return
        self.refresh()

    def _advance_milestone(self, milestone, status: str) -> None:
        self.service.set_milestone_status(milestone["id"], status)
        self.refresh()

    def _add_output(self) -> None:
        p = self.service.projects.get(self.project_id)
        dlg = OutputEditorDialog(self.service, p, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _edit_output(self, output) -> None:
        p = self.service.projects.get(self.project_id)
        dlg = OutputEditorDialog(self.service, p, output, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _delete_output(self, output) -> None:
        self.service.delete_output(output["id"])
        self.refresh()
