"""Practice / Project 对话框（Phase 4）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..services.capability import capability_label, capability_name
from ..services.practice import (
    ALL_MILESTONE_STATUSES,
    ALL_PROJECT_TYPES,
    OUTPUT_TYPES,
    MILESTONE_STATUS_LABELS,
    PROJECT_STATUS_LABELS,
    PROJECT_TYPE_LABELS,
    STATUS_IN_PROGRESS,
    STATUS_PLANNED,
    output_type_label,
)
from ..services.practice_project_service import PracticeError


def _check_list(items: list[tuple[int, str]], checked: set[int]) -> QListWidget:
    """items: [(id, label)]；返回带 checkbox 的列表。"""
    lw = QListWidget()
    for item_id, label in items:
        it = QListWidgetItem(label)
        it.setData(Qt.ItemDataRole.UserRole, item_id)
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(
            Qt.CheckState.Checked if item_id in checked
            else Qt.CheckState.Unchecked
        )
        lw.addItem(it)
    return lw


def _checked_ids(lw: QListWidget) -> list[int]:
    out = []
    for i in range(lw.count()):
        it = lw.item(i)
        if it.checkState() == Qt.CheckState.Checked:
            out.append(int(it.data(Qt.ItemDataRole.UserRole)))
    return out


class CreatePracticeProjectDialog(QDialog):
    """创建项目 shell（不要求 Skill/Topic/Milestone/Output）。"""

    def __init__(self, routes: list, route_skill_map: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建实践项目")
        self.setModal(True)
        self.resize(520, 560)
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：Qwen LoRA 微调")
        self.type_combo = QComboBox()
        for t in ALL_PROJECT_TYPES:
            self.type_combo.addItem(PROJECT_TYPE_LABELS.get(t, t), t)
        self.goal_edit = QLineEdit()
        self.goal_edit.setPlaceholderText("项目目标（可选）")
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setPlaceholderText("项目描述（可选）")
        self.desc_edit.setFixedHeight(70)
        form.addRow("项目名称 *", self.name_edit)
        form.addRow("项目类型 *", self.type_combo)
        form.addRow("项目目标", self.goal_edit)
        form.addRow("项目描述", self.desc_edit)
        root.addLayout(form)

        root.addWidget(QLabel("关联学习路线（可多选，可稍后再加）"))
        self.routes_list = _check_list(
            [(r.id, r.name) for r in routes], set()
        )
        root.addWidget(self.routes_list, stretch=1)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #e74c3c;")
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("创建")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            self.error_label.setText("项目名称不能为空。")
            self.error_label.setVisible(True)
            return
        self.accept()

    def result_payload(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "project_type": self.type_combo.currentData(),
            "goal": self.goal_edit.text().strip(),
            "description": self.desc_edit.toPlainText().strip(),
            "status": STATUS_PLANNED,
            "route_ids": _checked_ids(self.routes_list),
        }


class EditPracticeProjectDialog(QDialog):
    def __init__(self, project: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑项目")
        self.setModal(True)
        self.resize(460, 320)
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(project.get("name") or "")
        self.type_combo = QComboBox()
        for t in ALL_PROJECT_TYPES:
            self.type_combo.addItem(PROJECT_TYPE_LABELS.get(t, t), t)
        idx = self.type_combo.findData(project.get("project_type"))
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        self.status_combo = QComboBox()
        for s in ("planned", "in_progress", "completed"):
            self.status_combo.addItem(PROJECT_STATUS_LABELS.get(s, s), s)
        sidx = self.status_combo.findData(project.get("status"))
        if sidx >= 0:
            self.status_combo.setCurrentIndex(sidx)
        self.goal_edit = QLineEdit(project.get("goal") or "")
        self.desc_edit = QPlainTextEdit(project.get("description") or "")
        self.desc_edit.setFixedHeight(70)
        self.target_edit = QLineEdit(project.get("target_date") or "")
        self.target_edit.setPlaceholderText("YYYY-MM-DD（可选）")
        form.addRow("项目名称 *", self.name_edit)
        form.addRow("项目类型", self.type_combo)
        form.addRow("状态", self.status_combo)
        form.addRow("项目目标", self.goal_edit)
        form.addRow("项目描述", self.desc_edit)
        form.addRow("目标日期", self.target_edit)
        root.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "缺少名称", "项目名称不能为空。")
            return
        self.accept()

    def result_payload(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "project_type": self.type_combo.currentData(),
            "status": self.status_combo.currentData(),
            "goal": self.goal_edit.text().strip(),
            "description": self.desc_edit.toPlainText().strip(),
            "target_date": self.target_edit.text().strip() or None,
        }


class _ManageRelationsDialog(QDialog):
    def __init__(self, title: str, label: str, items, checked, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(480, 500)
        root = QVBoxLayout(self)
        root.addWidget(QLabel(label))
        self.list_widget = _check_list(items, checked)
        root.addWidget(self.list_widget, stretch=1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_accept(self) -> None:
        self.accept()

    def selected_ids(self) -> list[int]:
        return _checked_ids(self.list_widget)


class ManageProjectRoutesDialog(_ManageRelationsDialog):
    def __init__(self, project, service, routes, parent=None):
        self._service = service
        self._project = project
        checked = set(service.projects.list_route_ids(project["id"]))
        super().__init__(
            "关联学习路线", "勾选项目关联的 learning 路线（分组/已归档不可选）",
            [(r.id, r.name) for r in routes], checked, parent,
        )

    def _on_accept(self) -> None:
        try:
            self._service.set_routes(self._project["id"], self.selected_ids())
        except PracticeError as e:
            QMessageBox.warning(self, "无法保存", str(e))
            return
        self.accept()


class ManageProjectTopicsDialog(_ManageRelationsDialog):
    def __init__(self, project, service, plan_repo, routes, parent=None):
        self._service = service
        self._project = project
        self._plan_repo = plan_repo
        topics = []
        for r in routes:
            for t in plan_repo.list_topics_by_route(r.id):
                topics.append((t.id, f"{r.name} · {t.name}"))
        checked = set(service.projects.list_topic_ids(project["id"]))
        super().__init__(
            "关联 Topic", "只显示项目已关联路线下的 Topic",
            topics, checked, parent,
        )

    def _on_accept(self) -> None:
        try:
            self._service.set_topics(self._project["id"], self.selected_ids())
        except PracticeError as e:
            QMessageBox.warning(self, "无法保存", str(e))
            return
        self.accept()


class ManageProjectSkillsDialog(QDialog):
    """推荐技能（来自 project route_skills）+ 全局已有技能。"""

    def __init__(self, project, service, skill_repo, route_ids, parent=None):
        super().__init__(parent)
        self._service = service
        self._project = project
        self.setWindowTitle("关联技能")
        self.setModal(True)
        self.resize(520, 560)
        root = QVBoxLayout(self)
        checked = set(service.projects.list_skill_ids(project["id"]))

        recommended_ids: list[int] = []
        route_repo = service.route_repo
        for rid in route_ids:
            try:
                recommended_ids += route_repo.list_skill_ids(rid)
            except Exception:  # noqa: BLE001
                pass
        all_skills = skill_repo.list_all() if skill_repo is not None else []
        by_id = {s["id"]: s for s in all_skills}
        rec_items = [(sid, by_id[sid]["name"]) for sid in
                     dict.fromkeys(recommended_ids) if sid in by_id]

        root.addWidget(QLabel("推荐技能（来自项目路线 route_skills）"))
        self.rec_list = _check_list(rec_items, checked)
        self.rec_list.setMaximumHeight(160)
        root.addWidget(self.rec_list)

        root.addWidget(QLabel("其它已有技能（全局搜索）"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入以过滤…")
        root.addWidget(self.search_edit)
        others = [(s["id"], s["name"]) for s in all_skills
                  if s["id"] not in {i for i, _ in rec_items}]
        self.other_list = _check_list(others, checked)
        root.addWidget(self.other_list, stretch=1)
        self.search_edit.textChanged.connect(self._filter_others)

        self.hint = QLabel("添加项目技能不会创建 route_skills。")
        self.hint.setObjectName("TaskMeta")
        root.addWidget(self.hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _filter_others(self, text: str) -> None:
        text = (text or "").strip().lower()
        for i in range(self.other_list.count()):
            it = self.other_list.item(i)
            it.setHidden(bool(text) and text not in it.text().lower())

    def _on_accept(self) -> None:
        ids = set(_checked_ids(self.rec_list)) | set(_checked_ids(self.other_list))
        try:
            self._service.set_skills(self._project["id"], sorted(ids))
        except PracticeError as e:
            QMessageBox.warning(self, "无法保存", str(e))
            return
        self.accept()


class MilestoneEditorDialog(QDialog):
    def __init__(self, service, project, milestone=None, parent=None):
        super().__init__(parent)
        self._service = service
        self._project = project
        self._milestone = milestone
        self.setWindowTitle("编辑里程碑" if milestone else "新增里程碑")
        self.setModal(True)
        self.resize(440, 280)
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit(
            (milestone or {}).get("title") or ""
        )
        self.desc_edit = QPlainTextEdit(
            (milestone or {}).get("description") or ""
        )
        self.desc_edit.setFixedHeight(70)
        self.status_combo = QComboBox()
        for s in ALL_MILESTONE_STATUSES:
            self.status_combo.addItem(MILESTONE_STATUS_LABELS.get(s, s), s)
        idx = self.status_combo.findData(
            (milestone or {}).get("status") or "todo"
        )
        if idx >= 0:
            self.status_combo.setCurrentIndex(idx)
        form.addRow("标题 *", self.title_edit)
        form.addRow("描述", self.desc_edit)
        form.addRow("状态", self.status_combo)
        root.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_accept(self) -> None:
        if not self.title_edit.text().strip():
            QMessageBox.warning(self, "缺少标题", "里程碑标题不能为空。")
            return
        try:
            if self._milestone is None:
                self._service.add_milestone(
                    self._project["id"], self.title_edit.text().strip(),
                    self.desc_edit.toPlainText().strip(),
                    status=self.status_combo.currentData(),
                )
            else:
                self._service.update_milestone(
                    self._milestone["id"],
                    title=self.title_edit.text().strip(),
                    description=self.desc_edit.toPlainText().strip(),
                    status=self.status_combo.currentData(),
                )
        except PracticeError as e:
            QMessageBox.warning(self, "无法保存", str(e))
            return
        self.accept()


class OutputEditorDialog(QDialog):
    def __init__(self, service, project, output=None, parent=None):
        super().__init__(parent)
        self._service = service
        self._project = project
        self._output = output
        self.setWindowTitle("编辑成果" if output else "新增成果")
        self.setModal(True)
        self.resize(480, 420)
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.type_combo = QComboBox()
        for t in OUTPUT_TYPES:
            self.type_combo.addItem(output_type_label(t), t)
        if output is not None:
            idx = self.type_combo.findData(output.get("output_type"))
            if idx >= 0:
                self.type_combo.setCurrentIndex(idx)
        self.title_edit = QLineEdit((output or {}).get("title") or "")
        self.desc_edit = QPlainTextEdit((output or {}).get("description") or "")
        self.desc_edit.setFixedHeight(70)
        self.uri_edit = QLineEdit((output or {}).get("uri") or "")
        self.uri_edit.setPlaceholderText("可选：https://...（禁止包含凭据）")
        self.details_edit = QPlainTextEdit()
        self.details_edit.setPlaceholderText(
            "可选：结构化详情，每行 key=value（如 metric=loss / value=0.8）"
        )
        details = (output or {}).get("details") or {}
        self.details_edit.setPlainText(
            "\n".join(f"{k}={v}" for k, v in details.items())
        )
        form.addRow("类型 *", self.type_combo)
        form.addRow("标题 *", self.title_edit)
        form.addRow("描述", self.desc_edit)
        form.addRow("URI", self.uri_edit)
        form.addRow("详情", self.details_edit)
        root.addLayout(form)
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #e74c3c;")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _details(self) -> dict:
        out: dict = {}
        for line in self.details_edit.toPlainText().splitlines():
            line = line.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip():
                    out[k.strip()] = v.strip()
        return out

    def _on_accept(self) -> None:
        if not self.title_edit.text().strip():
            self.error_label.setText("成果标题不能为空。")
            self.error_label.setVisible(True)
            return
        payload = {
            "output_type": self.type_combo.currentData(),
            "title": self.title_edit.text().strip(),
            "description": self.desc_edit.toPlainText().strip(),
            "uri": self.uri_edit.text().strip() or None,
            "details": self._details(),
        }
        try:
            if self._output is None:
                self._service.add_output(self._project["id"], **payload)
            else:
                self._service.update_output(self._output["id"], **payload)
        except (PracticeError, ValueError) as e:
            self.error_label.setText(str(e))
            self.error_label.setVisible(True)
            return
        self.accept()


class PracticeTopicEvidenceDialog(QDialog):
    """确认项目使用 → 形成 PROJECT 能力证据（Phase 5）。

    文案原则：不写“系统已验证”；表达“用户提供并确认真实产出”。
    """

    def __init__(self, project, topic_id, topic_name, route_name, outputs,
                 capability_service, parent=None):
        super().__init__(parent)
        self._service = capability_service
        self._project = project
        self._topic_id = int(topic_id)
        self._outputs = outputs
        self.setWindowTitle("确认项目使用证据")
        self.setModal(True)
        self.resize(560, 620)

        root = QVBoxLayout(self)
        info = QLabel(
            f"项目：{project.get('name', '')}\n"
            f"Topic：{topic_name}\n"
            f"Route：{route_name or '—'}"
        )
        info.setObjectName("TaskMeta")
        info.setWordWrap(True)
        root.addWidget(info)

        hint = QLabel(
            "【项目使用证据】请如实填写该 Topic 在项目中的真实使用方式，"
            "并选择支撑成果。系统不会自动验证外部链接，"
            "证据来自你的确认与真实产出。"
        )
        hint.setObjectName("TaskMeta")
        hint.setWordWrap(True)
        root.addWidget(hint)

        root.addWidget(QLabel("项目使用说明 *"))
        self.usage_edit = QPlainTextEdit()
        self.usage_edit.setPlaceholderText(
            "例如：使用 LoRA 对 Qwen 模型进行参数高效微调，"
            "完成 adapter 训练、保存、加载和评估。"
        )
        self.usage_edit.setFixedHeight(90)
        root.addWidget(self.usage_edit)

        root.addWidget(QLabel("选择支撑成果（至少一个可作为主要证据）"))
        self.output_list = QListWidget()
        for o in outputs:
            role = self._service.output_role_label(o)
            label = f"{output_type_label(o.get('output_type'))} · {o.get('title')}　[{role}]"
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, int(o["id"]))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(
                Qt.CheckState.Checked if role == "主要证据"
                else Qt.CheckState.Unchecked
            )
            self.output_list.addItem(it)
        root.addWidget(self.output_list, stretch=1)

        self.confirm_check = QCheckBox(
            "我确认该 Topic 确实在此项目中实际使用，并由以上项目成果支撑。"
        )
        root.addWidget(self.confirm_check)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #e74c3c;")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "形成项目能力证据"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def selected_output_ids(self) -> list[int]:
        return _checked_ids(self.output_list)

    def _on_accept(self) -> None:
        if not self.usage_edit.toPlainText().strip():
            self._err("必须填写项目使用说明。")
            return
        if not self.selected_output_ids():
            self._err("至少选择一个支撑成果。")
            return
        if not self.confirm_check.isChecked():
            self._err("必须显式确认该 Topic 确实在此项目中实际使用。")
            return
        try:
            self._result = self._service.create_project_topic_evidence(
                self._project["id"], self._topic_id,
                self.selected_output_ids(),
                self.usage_edit.toPlainText().strip(),
                confirmed=True,
            )
        except Exception as e:  # noqa: BLE001
            self._err(str(e))
            return
        self.accept()

    def result_evidence(self) -> dict:
        return getattr(self, "_result", {})

    def _err(self, msg: str) -> None:
        self.error_label.setText(msg)
        self.error_label.setVisible(True)


class RevokeEvidenceDialog(QDialog):
    """撤销项目使用证据（保留历史行，仅置 is_active=0）。"""

    def __init__(self, topic_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"撤销项目使用证据 · {topic_name}")
        self.setModal(True)
        self.resize(440, 220)
        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "撤销后当前能力会回落到其它仍生效的证据；\n"
            "原始证据与产出关系会保留为历史，不会被物理删除。"
        ))
        root.addWidget(QLabel("撤销原因（可选）"))
        self.reason_edit = QPlainTextEdit()
        self.reason_edit.setFixedHeight(70)
        root.addWidget(self.reason_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("撤销证据")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def reason(self) -> str:
        return self.reason_edit.toPlainText().strip()


class PracticeTopicRequirementDialog(QDialog):
    """Phase 6：设置项目学习要求（目标能力只能 1~4，禁止 PROJECT）。"""

    def __init__(self, project, topic_id, topic_name, readiness_service,
                 existing=None, parent=None):
        super().__init__(parent)
        self._readiness = readiness_service
        self._project = project
        self._topic_id = int(topic_id)
        self.setWindowTitle("设置学习要求")
        self.setModal(True)
        self.resize(480, 360)
        root = QVBoxLayout(self)
        info = QLabel(
            f"项目：{project.get('name', '')}\nTopic：{topic_name}"
        )
        info.setObjectName("TaskMeta")
        info.setWordWrap(True)
        root.addWidget(info)

        root.addWidget(QLabel("项目最低能力要求"))
        from PySide6.QtWidgets import QRadioButton

        from ..services.practice import REQUIREMENT_TARGET_LEVELS

        self._buttons: dict[int, object] = {}
        self._group = QButtonGroup(self)
        current = int((existing or {}).get("target_capability_level") or 2)
        for level in REQUIREMENT_TARGET_LEVELS:
            rb = QRadioButton(
                f"{capability_label(level)}（{capability_name(level)}）"
            )
            if level == current:
                rb.setChecked(True)
            self._buttons[level] = rb
            self._group.addButton(rb, level)
            root.addWidget(rb)

        root.addWidget(QLabel("备注（可选）"))
        self.note_edit = QPlainTextEdit((existing or {}).get("note") or "")
        self.note_edit.setFixedHeight(60)
        root.addWidget(self.note_edit)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #e74c3c;")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存要求")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def selected_level(self) -> int:
        for level, rb in self._buttons.items():
            if rb.isChecked():
                return int(level)
        return 2

    def _on_accept(self) -> None:
        try:
            self._readiness.set_requirement(
                self._project["id"], self._topic_id, self.selected_level(),
                note=self.note_edit.toPlainText().strip(),
            )
        except Exception as e:  # noqa: BLE001
            self.error_label.setText(str(e))
            self.error_label.setVisible(True)
            return
        self.accept()
