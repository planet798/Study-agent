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
from .dialogs import show_warning
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

    def __init__(self, route, route_service, route_plan_service, parent=None):
        super().__init__(parent)
        self.route = route
        self.route_service = route_service
        self.route_plan_service = route_plan_service
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
                self.body_layout.addWidget(
                    _secondary("创建手动学习计划", self._on_create_plan)
                )
            self.body_layout.addStretch()
            return

        progress = self.route_plan_service.route_progress(route.id)
        prog = QLabel(
            f"已完成 Topic：{progress['done']} / {progress['total']}"
            f"　阶段数：{progress['phases']}"
        )
        prog.setObjectName("TaskMeta")
        self.body_layout.addWidget(prog)

        if not structure.phases:
            self.body_layout.addWidget(
                _secondary("＋ 添加阶段", self._on_add_phase)
            )

        done_ids = self.route_plan_service.done_topic_ids()
        for phase in structure.phases:
            self.body_layout.addWidget(self._phase_card(phase, done_ids))
        self.body_layout.addStretch()

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
            if not self.route.is_archived:
                row.addWidget(_secondary(
                    "删除", lambda _=False, t=topic: self._on_delete_topic(t)
                ))
            lay.addLayout(row)
        return card

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


class LearningRoutesPage(QWidget):
    """学习路线总览页面。"""

    def __init__(self, route_service, route_plan_service=None, parent=None):
        super().__init__(parent)
        self.route_service = route_service
        self.route_plan_service = route_plan_service
        self.show_archived = False
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("学习路线")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
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
        meta = QLabel(
            f"类型：分组　子路线：{len(active_children)}　"
            f"优先级：{priority_text(route.priority)}"
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
        done_ids = self.route_plan_service.done_topic_ids()
        for phase in structure.phases:
            if any(t.id not in done_ids for t in phase.topics):
                return phase.name
        return structure.phases[-1].name

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
            route, self.route_service, self.route_plan_service, parent=self
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
