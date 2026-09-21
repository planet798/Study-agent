"""实践项目页面与详情（Phase 4）。

独立一级页面；Practice 不放进学习路线树。
Project Detail 更新 milestone/output 后保持自身滚动位置。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
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
    RevokeEvidenceDialog,
)
from .styles import apply_secondary_button_text


def _secondary(text: str, slot) -> QPushButton:
    btn = QPushButton(text)
    btn.setObjectName("SecondaryButton")
    apply_secondary_button_text(btn)
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
    ("全部", None),
    ("进行中", STATUS_IN_PROGRESS),
    ("计划中", STATUS_PLANNED),
    ("已完成", STATUS_COMPLETED),
    ("已归档", STATUS_ARCHIVED),
)


class PracticeProjectsPage(QWidget):
    def __init__(self, practice_service, route_repo, skill_repo, plan_repo,
                 capability_service=None, parent=None):
        super().__init__(parent)
        self.service = practice_service
        self.route_repo = route_repo
        self.skill_repo = skill_repo
        self.plan_repo = plan_repo
        self.capability_service = capability_service
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("实践项目")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        head.addSpacing(12)
        head.addWidget(QLabel("状态"))
        self.filter_combo = QComboBox()
        for label, value in _FILTERS:
            self.filter_combo.addItem(label, value)
        self.filter_combo.currentIndexChanged.connect(lambda *_: self.refresh())
        head.addWidget(self.filter_combo)
        head.addStretch()
        head.addWidget(_secondary("＋ 新建项目", self._on_create))
        root.addLayout(head)

        hint = QLabel(
            "实践项目用于把多条路线 / 技能 / Topic 组合成可验证的真实成果；"
            "项目不参与每日排程与能力等级判定。"
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
        _clear_layout(self.list_layout)

        status = self.filter_combo.currentData()
        if status is None:
            projects = self.service.projects.list_active()
        else:
            projects = self.service.list_projects(status=status)
        if not projects:
            empty = QLabel("暂无实践项目")
            empty.setObjectName("EmptyHint")
            self.list_layout.addWidget(empty)
        for p in projects:
            self.list_layout.addWidget(self._project_card(p))
        self.list_layout.addStretch()

    def _project_card(self, project: dict) -> QWidget:
        card = QFrame()
        card.setObjectName("TaskCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(3)

        top = QHBoxLayout()
        name = QLabel(project["name"])
        name.setObjectName("TaskTitle")
        top.addWidget(name)
        top.addStretch()
        lay.addLayout(top)

        route_ids = self.service.projects.list_route_ids(project["id"])
        route_names = []
        if self.route_repo is not None:
            for rid in route_ids:
                r = self.route_repo.get(rid)
                if r is not None:
                    route_names.append(r.name)
        prog = self.service.get_project_progress(project["id"])
        ms_txt = (f"{prog['milestones_done']} / {prog['milestones_total']}"
                  if prog["has_milestones"] else "尚未设置里程碑")
        evidence_txt = ""
        if self.capability_service is not None:
            try:
                n = self.capability_service.count_active_by_project(project["id"])
                evidence_txt = f"项目能力证据：{n}"
            except Exception:  # noqa: BLE001
                evidence_txt = ""
        meta = QLabel(
            f"状态：{project_status_label(project['status'])}　"
            f"类型：{project_type_label(project['project_type'])}\n"
            f"关联路线：{' · '.join(route_names) or '—'}\n"
            f"里程碑：{ms_txt}　成果：{prog['output_count']}"
            + (f"　{evidence_txt}" if evidence_txt else "")
        )
        meta.setObjectName("TaskMeta")
        meta.setWordWrap(True)
        lay.addWidget(meta)

        row = QHBoxLayout()
        row.addWidget(_secondary(
            "查看项目", lambda _=False, p=project: self._open_detail(p)
        ))
        row.addWidget(_secondary(
            "编辑", lambda _=False, p=project: self._edit(p)
        ))
        if project["status"] != STATUS_ARCHIVED:
            row.addWidget(_secondary(
                "归档", lambda _=False, p=project: self._archive(p)
            ))
        else:
            row.addWidget(_secondary(
                "恢复", lambda _=False, p=project: self._restore(p)
            ))
        row.addStretch()
        lay.addLayout(row)
        return card

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
                 capability_service=None, parent=None):
        super().__init__(parent)
        self.project_id = project_id
        self.service = service
        self.route_repo = route_repo
        self.skill_repo = skill_repo
        self.plan_repo = plan_repo
        self.capability_service = capability_service
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

    def refresh(self) -> None:
        value = self.scroll.verticalScrollBar().value()
        _clear_layout(self.body_layout)
        detail = self.service.get_project_detail(self.project_id)
        p = detail["project"]
        self.title_label.setText(p["name"])
        prog = self.service.get_project_progress(self.project_id)
        ms_txt = (f"{prog['milestones_done']} / {prog['milestones_total']}"
                  if prog["has_milestones"] else "尚未设置里程碑")
        overview = QLabel(
            f"【项目概览】\n状态：{project_status_label(p['status'])}　"
            f"类型：{project_type_label(p['project_type'])}\n"
            f"目标：{p.get('goal') or '—'}\n描述：{p.get('description') or '—'}\n"
            f"里程碑：{ms_txt}　成果：{prog['output_count']}"
        )
        overview.setObjectName("TaskMeta")
        overview.setWordWrap(True)
        self.body_layout.addWidget(overview)

        self._routes_section(detail["routes"])
        self._skills_section(detail["skills"])
        self._topics_section(detail["topics"])
        self._milestones_section(detail["milestones"])
        self._outputs_section(detail["outputs"])
        self._evidence_section()
        self.body_layout.addStretch()
        QTimer.singleShot(0, lambda: self._restore_scroll(value))
        QTimer.singleShot(60, lambda: self._restore_scroll(value))

    def _restore_scroll(self, value: int) -> None:
        value = int(value)
        if value <= 0:
            return

        def _apply() -> None:
            bar = self.scroll.verticalScrollBar()
            if bar.maximum() >= value:
                bar.setValue(value)

        # 布局可能晚于 refresh 完成：多帧重试（不依赖信号连接，避免累积/警告）
        for delay in (0, 50, 150, 400):
            QTimer.singleShot(delay, _apply)

    def _routes_section(self, routes) -> None:
        head = QHBoxLayout()
        head.addWidget(self._section("【关联学习路线】"))
        head.addStretch()
        head.addWidget(_secondary("管理路线", self._manage_routes))
        self.body_layout.addLayout(head)
        names = " · ".join(r.name for r in routes) if routes else "—"
        lbl = QLabel(names)
        lbl.setObjectName("TaskMeta")
        lbl.setWordWrap(True)
        self.body_layout.addWidget(lbl)

    def _skills_section(self, skills) -> None:
        head = QHBoxLayout()
        head.addWidget(self._section("【关联技能】"))
        head.addStretch()
        head.addWidget(_secondary("管理技能", self._manage_skills))
        self.body_layout.addLayout(head)
        names = "、".join(s["name"] for s in skills) if skills else "—"
        lbl = QLabel(names)
        lbl.setObjectName("TaskMeta")
        lbl.setWordWrap(True)
        self.body_layout.addWidget(lbl)

    def _topics_section(self, topics) -> None:
        head = QHBoxLayout()
        head.addWidget(self._section("【关联 Topic】"))
        head.addStretch()
        head.addWidget(_secondary("管理 Topic", self._manage_topics))
        self.body_layout.addLayout(head)
        names = "、".join(t["name"] for t in topics) if topics else "—"
        lbl = QLabel(names)
        lbl.setObjectName("TaskMeta")
        lbl.setWordWrap(True)
        self.body_layout.addWidget(lbl)

    def _milestones_section(self, milestones) -> None:
        head = QHBoxLayout()
        head.addWidget(self._section("【里程碑】"))
        head.addStretch()
        head.addWidget(_secondary("＋ 新增里程碑", self._add_milestone))
        self.body_layout.addLayout(head)
        if not milestones:
            lbl = QLabel("尚未设置里程碑")
            lbl.setObjectName("TaskMeta")
            self.body_layout.addWidget(lbl)
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
            self.body_layout.addLayout(row)

    def _outputs_section(self, outputs) -> None:
        head = QHBoxLayout()
        head.addWidget(self._section("【项目成果】"))
        head.addStretch()
        head.addWidget(_secondary("＋ 新增成果", self._add_output))
        self.body_layout.addLayout(head)
        if not outputs:
            lbl = QLabel("暂无项目成果")
            lbl.setObjectName("TaskMeta")
            self.body_layout.addWidget(lbl)
            return
        for o in outputs:
            row = QHBoxLayout()
            uri = f"　{o['uri']}" if o.get("uri") else ""
            lbl = QLabel(
                f"[{output_type_label(o['output_type'])}] {o['title']}{uri}"
            )
            lbl.setObjectName("TaskMeta")
            lbl.setWordWrap(True)
            row.addWidget(lbl, stretch=1)
            row.addWidget(_secondary(
                "编辑", lambda _=False, oo=o: self._edit_output(oo)
            ))
            row.addWidget(_secondary(
                "删除", lambda _=False, oo=o: self._delete_output(oo)
            ))
            self.body_layout.addLayout(row)

    # ---------- Phase 5：项目能力证据 ----------

    def _evidence_section(self) -> None:
        head = QHBoxLayout()
        head.addWidget(self._section("【项目能力证据】"))
        head.addStretch()
        self.body_layout.addLayout(head)
        if self.capability_service is None:
            lbl = QLabel("未启用项目能力证据")
            lbl.setObjectName("TaskMeta")
            self.body_layout.addWidget(lbl)
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
            self.body_layout.addWidget(lbl)
            return
        for c in candidates:
            row = QHBoxLayout()
            if c["has_active_evidence"]:
                ev = self.capability_service.get_topic_evidence(
                    c["active_evidence_id"]
                ) or {}
                outputs = " · ".join(ev.get("output_labels") or [])
                lbl = QLabel(
                    f"{c['topic_name']}\n✓ 已在真实项目中使用\n"
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
            self.body_layout.addLayout(row)

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
