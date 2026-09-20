"""Topic 学习组成编辑对话框（Phase 2）。

展示并编辑一个 Topic 的 learning components：
- 5 种 activity：theory / code_reading / experiment / interview / practice
- 每项可：启用/停用、required/optional、调整顺序
- 至少保留一个“启用且必需”的活动
- 已有关联任务的 component 不能物理删除（只能停用）
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..services.learning_activity import (
    ACTIVITY_DESCRIPTIONS,
    ACTIVITY_LABELS,
    ALL_ACTIVITY_KINDS,
)
from ..services.topic_learning_profile_service import ActivityProfileError


class TopicLearningProfileDialog(QDialog):
    """编辑某 Topic 的学习活动 profile。"""

    def __init__(self, topic, topic_learning_service, parent=None):
        super().__init__(parent)
        self.topic = topic
        self.service = topic_learning_service
        self.setWindowTitle(f"学习组成 · {getattr(topic, 'name', '')}")
        self.setModal(True)
        self.resize(560, 520)

        self._rows: dict[str, dict] = {}
        self._order: list[str] = list(ALL_ACTIVITY_KINDS)

        root = QVBoxLayout(self)
        hint = QLabel(
            "配置该知识点的学习方式。每个 Topic 至少需要保留一个“启用且必需”"
            "的学习活动；已有关联任务的活动只能停用，不能删除。"
        )
        hint.setObjectName("TaskMeta")
        hint.setWordWrap(True)
        root.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 6, 0)
        scroll.setWidget(body)
        root.addWidget(scroll, stretch=1)

        self._build_rows()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ---------- 构建 ----------

    def _build_rows(self) -> None:
        existing = {
            c["activity_kind"]: c
            for c in self.service.get_components(self.topic.id)
        }
        existing_keys = set(existing)
        # 顺序：已有 component 按 order_index，其余按默认顺序
        ordered = sorted(
            ALL_ACTIVITY_KINDS,
            key=lambda k: (
                existing[k]["order_index"] if k in existing else 100,
                ALL_ACTIVITY_KINDS.index(k),
            ),
        )
        self._order = ordered

        for kind in ordered:
            comp = existing.get(kind)
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(4, 2, 4, 2)

            enabled_cb = QCheckBox()
            enabled_cb.setChecked(bool(comp["enabled"]) if comp else False)
            if comp and self.service.repo.has_active_linked_tasks(comp["id"]):
                enabled_cb.setToolTip("存在未完成任务，不能停用")
            lay.addWidget(enabled_cb)

            name = QLabel(f"{ACTIVITY_LABELS[kind]}")
            name.setMinimumWidth(80)
            lay.addWidget(name)

            required_cb = QCheckBox("必需")
            required_cb.setChecked(bool(comp["required"]) if comp else False)
            lay.addWidget(required_cb)

            desc = QLabel(ACTIVITY_DESCRIPTIONS.get(kind, ""))
            desc.setObjectName("TaskMeta")
            lay.addWidget(desc, stretch=1)

            up = QPushButton("↑")
            up.setFixedWidth(30)
            down = QPushButton("↓")
            down.setFixedWidth(30)
            up.clicked.connect(lambda _=False, k=kind: self._move(k, -1))
            down.clicked.connect(lambda _=False, k=kind: self._move(k, 1))
            lay.addWidget(up)
            lay.addWidget(down)

            self.body_layout.addWidget(row)
            self._rows[kind] = {
                "enabled": enabled_cb,
                "required": required_cb,
                "row": row,
            }
        self.body_layout.addStretch()

    def _move(self, kind: str, delta: int) -> None:
        idx = self._order.index(kind)
        new_idx = max(0, min(len(self._order) - 1, idx + delta))
        if new_idx == idx:
            return
        self._order.pop(idx)
        self._order.insert(new_idx, kind)
        self._rebuild()

    def _rebuild(self) -> None:
        # 保存当前 UI 状态，重建时恢复
        state = {
            k: (r["enabled"].isChecked(), r["required"].isChecked())
            for k, r in self._rows.items()
        }
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._rows.clear()
        existing = {
            c["activity_kind"]: c
            for c in self.service.get_components(self.topic.id)
        }
        for kind in self._order:
            comp = existing.get(kind)
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(4, 2, 4, 2)
            enabled_cb = QCheckBox()
            enabled_cb.setChecked(state[kind][0])
            lay.addWidget(enabled_cb)
            name = QLabel(ACTIVITY_LABELS[kind])
            name.setMinimumWidth(80)
            lay.addWidget(name)
            required_cb = QCheckBox("必需")
            required_cb.setChecked(state[kind][1])
            lay.addWidget(required_cb)
            desc = QLabel(ACTIVITY_DESCRIPTIONS.get(kind, ""))
            desc.setObjectName("TaskMeta")
            lay.addWidget(desc, stretch=1)
            up = QPushButton("↑")
            up.setFixedWidth(30)
            down = QPushButton("↓")
            down.setFixedWidth(30)
            up.clicked.connect(lambda _=False, k=kind: self._move(k, -1))
            down.clicked.connect(lambda _=False, k=kind: self._move(k, 1))
            lay.addWidget(up)
            lay.addWidget(down)
            self.body_layout.addWidget(row)
            self._rows[kind] = {
                "enabled": enabled_cb, "required": required_cb, "row": row,
            }
        self.body_layout.addStretch()

    # ---------- 保存 ----------

    def desired_state(self) -> dict[str, tuple[bool, bool]]:
        return {
            k: (r["enabled"].isChecked(), r["required"].isChecked())
            for k, r in self._rows.items()
        }

    def validate(self) -> str | None:
        state = self.desired_state()
        if not any(en and req for en, req in state.values()):
            return "至少需要保留一个“启用且必需”的学习活动。"
        return None

    def _on_save(self) -> None:
        err = self.validate()
        if err:
            QMessageBox.warning(self, "无法保存", err)
            return
        state = self.desired_state()
        try:
            self.service.set_profile_state(
                self.topic.id, state, self._order
            )
        except ActivityProfileError as e:
            QMessageBox.warning(self, "无法保存", str(e))
            return
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(e))
            return
        self.accept()
