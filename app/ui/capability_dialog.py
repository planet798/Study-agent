"""Capability Evidence UI（Phase 3）。

- CapabilityEvidenceDialog：只读证据时间线。
- ExperimentOutcomeDialog：为 done experiment task 记录真实实验成果
  （复用 LearningOutcomeService，保存 learning_outcomes(kind='experiment')，
   满足严格证据规则时由 CapabilityService 提取 EXPERIMENT）。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..services.capability import capability_label


def _readonly_box(text: str) -> QPlainTextEdit:
    box = QPlainTextEdit()
    box.setReadOnly(True)
    box.setPlainText(text)
    return box


class CapabilityEvidenceDialog(QDialog):
    """某个知识点的 capability 证据时间线（只读）。"""

    def __init__(self, kp_name: str, kp_id: int, capability_service, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"能力证据 · {kp_name}")
        self.setModal(True)
        self.resize(640, 520)

        root = QVBoxLayout(self)
        self.current_label = QLabel("")
        self.current_label.setObjectName("SectionTitle")
        root.addWidget(self.current_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 6, 0)
        scroll.setWidget(body)
        root.addWidget(scroll, stretch=1)

        self._render(capability_service, kp_id)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

    def _render(self, service, kp_id: int) -> None:
        cap = service.get_current_capability(kp_id)
        self.current_label.setText(
            f"当前能力：{cap['label']}（{cap['name']}）"
        )
        evidence = service.list_evidence(kp_id)
        active = [e for e in evidence if e["is_active"]]
        if not active:
            empty = QLabel("暂无能力证据")
            empty.setObjectName("EmptyHint")
            self.body_layout.addWidget(empty)
            self.body_layout.addStretch()
            return
        for e in active:
            level = int(e["capability_level"])
            src = e.get("evidence_type")
            desc = e.get("description") or ""
            ts = (e.get("created_at") or "")[:10]
            block = QLabel(
                f"{ts}\n{capability_label(level)}\n来源：{src}\n{desc}"
            )
            block.setObjectName("TaskMeta")
            block.setWordWrap(True)
            self.body_layout.addWidget(block)
        self.body_layout.addStretch()


class ExperimentOutcomeDialog(QDialog):
    """记录实验成果（真实产物），保存后可提取 EXPERIMENT 能力。"""

    def __init__(self, kp_name: str, kp_id: int, outcome_service,
                 task_repo, parent=None):
        super().__init__(parent)
        self.kp_name = kp_name
        self.kp_id = kp_id
        self.outcome_service = outcome_service
        self.task_repo = task_repo
        self.setWindowTitle(f"记录实验成果 · {kp_name}")
        self.setModal(True)
        self.resize(560, 520)

        root = QVBoxLayout(self)
        hint = QLabel(
            "记录真实实验成果（不是“我做完了”这类文字）。至少需要："
            "实验结果说明 + 一项可验证产物（metrics / git / github / dataset）。"
        )
        hint.setObjectName("TaskMeta")
        hint.setWordWrap(True)
        root.addWidget(hint)

        form = QFormLayout()
        self.title_edit = QLineEdit(f"{kp_name} 实验")
        self.result_edit = QPlainTextEdit()
        self.result_edit.setPlaceholderText(
            "实验结果说明：做了什么实验、观察到什么现象/结果"
        )
        self.metrics_edit = QPlainTextEdit()
        self.metrics_edit.setPlaceholderText(
            "可选：metrics，每行 key=value，例如\nloss=0.42\nacc=0.91"
        )
        self.git_edit = QLineEdit()
        self.git_edit.setPlaceholderText("可选：git commit")
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("可选：GitHub / 报告 URL")
        self.dataset_edit = QLineEdit()
        self.dataset_edit.setPlaceholderText("可选：dataset / benchmark 名称")
        form.addRow("标题 *", self.title_edit)
        form.addRow("实验结果 *", self.result_edit)
        form.addRow("Metrics", self.metrics_edit)
        form.addRow("Git commit", self.git_edit)
        form.addRow("URL", self.url_edit)
        form.addRow("Dataset", self.dataset_edit)
        root.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #e74c3c;")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _parse_metrics(self) -> dict:
        out: dict = {}
        for line in self.metrics_edit.toPlainText().splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if not k:
                continue
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = v
        return out

    def _find_done_experiment_task(self):
        conn = getattr(self.task_repo, "conn", None)
        if conn is None:
            return None
        row = conn.execute(
            "SELECT id FROM tasks WHERE knowledge_point_id = ? "
            "AND status='done' AND learning_activity_kind='experiment' "
            "ORDER BY id DESC LIMIT 1",
            (int(self.kp_id),),
        ).fetchone()
        return int(row[0]) if row else None

    def _on_save(self) -> None:
        if not self.title_edit.text().strip():
            self._err("实验标题不能为空。")
            return
        if not self.result_edit.toPlainText().strip():
            self._err("必须填写实验结果说明。")
            return
        metrics = self._parse_metrics()
        has_artifact = bool(metrics) or bool(self.git_edit.text().strip()) \
            or bool(self.url_edit.text().strip()) \
            or bool(self.dataset_edit.text().strip())
        if not has_artifact:
            self._err("至少需要一项产物：metrics / git / URL / dataset。")
            return
        task_id = self._find_done_experiment_task()
        if task_id is None:
            self._err("未找到已完成的 experiment 活动任务，无法登记实验能力证据。")
            return
        try:
            self.outcome_service.create_manual_outcome(
                kind="experiment",
                title=self.title_edit.text().strip(),
                content=self.result_edit.toPlainText().strip(),
                metrics=metrics,
                git_commit=self.git_edit.text().strip() or None,
                github_url=self.url_edit.text().strip(),
                dataset=self.dataset_edit.text().strip(),
                linked_kp_id=self.kp_id,
                task_id=task_id,
            )
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(e))
            return
        self.accept()

    def _err(self, msg: str) -> None:
        self.error_label.setText(msg)
        self.error_label.setVisible(True)
