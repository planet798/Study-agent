"""AI 学习路线 Builder 对话框（Phase F）。

- AIRouteBuilderDialog：收集生成目标 / 基础 / 重点 / 用途 / 深度（纯输入）；
- RouteDraftPreviewDialog：展示 AI 草稿，允许用户编辑 Phase/Topic，
  确认后返回新的 AIRouteDraft（**不写数据库**）。

AI 只产生草稿；持久化由主线程 RoutePlanService 完成。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..ai.schemas import (
    AIPhaseDraft,
    AIRouteDraft,
    AITopicDraft,
    MAX_TOPIC_MINUTES,
    MIN_TOPIC_MINUTES,
)
from .dialogs import show_warning
from .styles import apply_secondary_button_text

PURPOSES = ("系统学习", "实习 / 面试准备", "快速入门")
DEPTHS = ("基础", "中等", "深入")


def _secondary(text, on_click):
    btn = QPushButton(text)
    btn.setObjectName("SecondaryButton")
    apply_secondary_button_text(btn)
    btn.clicked.connect(on_click)
    return btn


class AIRouteBuilderDialog(QDialog):
    def __init__(self, route_name: str, route_goal: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"AI 生成学习计划 · {route_name}")
        self.setModal(True)
        self.resize(560, 520)
        self._route_name = route_name

        root = QVBoxLayout(self)
        root.setSpacing(8)
        title = QLabel(f"为「{route_name}」生成学习路线草稿")
        title.setObjectName("SectionTitle")
        root.addWidget(title)

        hint = QLabel(
            "AI 只生成草稿，确认前不会写入数据库；生成后可在预览中编辑。"
        )
        hint.setObjectName("TaskMeta")
        hint.setWordWrap(True)
        root.addWidget(hint)

        root.addWidget(QLabel("学习目标"))
        self.goal_edit = QLineEdit(route_goal or "")
        self.goal_edit.setPlaceholderText("例如：准备算法实习面试，掌握 RL 基础到 PPO")
        root.addWidget(self.goal_edit)

        root.addWidget(QLabel("当前基础 / 已会内容（可选）"))
        self.background_edit = QPlainTextEdit()
        self.background_edit.setFixedHeight(70)
        root.addWidget(self.background_edit)

        root.addWidget(QLabel("重点内容（可选）"))
        self.focus_edit = QPlainTextEdit()
        self.focus_edit.setFixedHeight(70)
        root.addWidget(self.focus_edit)

        row = QHBoxLayout()
        row.addWidget(QLabel("用途"))
        self.purpose_combo = QComboBox()
        for p in PURPOSES:
            self.purpose_combo.addItem(p, p)
        row.addWidget(self.purpose_combo)
        row.addSpacing(12)
        row.addWidget(QLabel("期望深度"))
        self.depth_combo = QComboBox()
        for d in DEPTHS:
            self.depth_combo.addItem(d, d)
        self.depth_combo.setCurrentIndex(1)
        row.addWidget(self.depth_combo)
        row.addStretch()
        root.addLayout(row)

        root.addStretch()

        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(_secondary("取消", self.reject))
        self.generate_btn = _secondary("生成草稿", self._on_generate)
        btns.addWidget(self.generate_btn)
        root.addLayout(btns)

    def _on_generate(self) -> None:
        if not self.goal_edit.text().strip():
            show_warning(self, "请先填写学习目标。")
            return
        self.accept()

    def result_payload(self) -> dict:
        return {
            "route_name": self._route_name,
            "route_goal": self.goal_edit.text().strip(),
            "background": self.background_edit.toPlainText().strip(),
            "focus": self.focus_edit.toPlainText().strip(),
            "purpose": self.purpose_combo.currentData(),
            "depth": self.depth_combo.currentData(),
        }


class RouteDraftPreviewDialog(QDialog):
    """AI 草稿预览 / 编辑；确认后返回 AIRouteDraft。"""

    def __init__(self, draft: AIRouteDraft, mode: str = "no_plan", parent=None):
        super().__init__(parent)
        self._mode = mode
        self.replace_empty = False
        self._model = self._from_draft(draft)
        self.setWindowTitle("预览 AI 学习计划")
        self.setModal(True)
        self.resize(760, 720)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        title = QLabel("预览 / 编辑 AI 学习计划（确认前不会写入数据库）")
        title.setObjectName("SectionTitle")
        root.addWidget(title)

        if draft.summary:
            summary = QLabel(f"组织思路：{draft.summary}")
            summary.setObjectName("TaskMeta")
            summary.setWordWrap(True)
            root.addWidget(summary)

        plan_row = QHBoxLayout()
        plan_row.addWidget(QLabel("计划名称"))
        self.plan_name_edit = QLineEdit(draft.plan_name)
        plan_row.addWidget(self.plan_name_edit, 1)
        root.addLayout(plan_row)

        if mode == "empty_plan":
            self.replace_check = QCheckBox("替换当前空计划（当前为空，无历史记录）")
            self.replace_check.setChecked(True)
            root.addWidget(self.replace_check)
        else:
            self.replace_check = None

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 6, 0)
        self.body_layout.setSpacing(8)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, stretch=1)

        add_row = QHBoxLayout()
        add_row.addWidget(_secondary("＋ 添加阶段", self._on_add_phase))
        add_row.addStretch()
        root.addLayout(add_row)

        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(_secondary("取消", self.reject))
        btns.addWidget(_secondary("确认创建", self._on_confirm))
        root.addLayout(btns)

        self._render()

    # ---------- 数据 ----------

    @staticmethod
    def _from_draft(draft: AIRouteDraft) -> list[dict]:
        return [
            {
                "name": p.name,
                "goal": p.goal,
                "order": p.order,
                "topics": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "estimated_minutes": t.estimated_minutes,
                        "priority": t.priority,
                        "order": t.order,
                    }
                    for t in p.topics
                ],
            }
            for p in draft.phases
        ]

    def draft(self) -> AIRouteDraft:
        """读取当前 UI（含用户编辑）为 AIRouteDraft。"""
        self._collect()
        phases = []
        for pi, p in enumerate(self._model):
            topics = tuple(
                AITopicDraft(
                    name=t["name"].strip(),
                    description=t["description"].strip(),
                    estimated_minutes=int(t["estimated_minutes"]),
                    priority=int(t["priority"]),
                    order=int(t["order"]),
                )
                for t in p["topics"]
            )
            phases.append(AIPhaseDraft(
                name=p["name"].strip(), goal=p["goal"].strip(),
                order=int(p["order"]), topics=topics,
            ))
        return AIRouteDraft(
            route_name="",  # 由调用方填充
            plan_name=self.plan_name_edit.text().strip(),
            summary="",
            phases=tuple(phases),
        )

    # ---------- 渲染 ----------

    def _collect(self) -> None:
        for pi, pw in enumerate(self._phase_widgets):
            self._model[pi]["name"] = pw["name"].text()
            self._model[pi]["goal"] = pw["goal"].text()
            self._model[pi]["order"] = pw["order"].value()
            for ti, tw in enumerate(pw["topics"]):
                self._model[pi]["topics"][ti]["name"] = tw["name"].text()
                self._model[pi]["topics"][ti]["description"] = \
                    tw["description"].toPlainText()
                self._model[pi]["topics"][ti]["estimated_minutes"] = \
                    tw["minutes"].value()
                self._model[pi]["topics"][ti]["priority"] = tw["priority"].value()
                self._model[pi]["topics"][ti]["order"] = tw["order"].value()

    def _render(self) -> None:
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._phase_widgets: list[dict] = []
        for pi, phase in enumerate(self._model):
            self.body_layout.addWidget(self._phase_widget(pi, phase))
        self.body_layout.addStretch()

    def _phase_widget(self, pi: int, phase: dict) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        head = QHBoxLayout()
        name = QLineEdit(phase["name"])
        name.setPlaceholderText("阶段名称")
        head.addWidget(name, 3)
        goal = QLineEdit(phase["goal"])
        goal.setPlaceholderText("阶段目标")
        head.addWidget(goal, 4)
        head.addWidget(QLabel("顺序"))
        order = QSpinBox()
        order.setRange(1, 99)
        order.setValue(int(phase["order"]))
        head.addWidget(order)
        head.addWidget(_secondary(
            "删除阶段", lambda _=False, i=pi: self._on_delete_phase(i)
        ))
        head.addWidget(_secondary(
            "＋ 知识点", lambda _=False, i=pi: self._on_add_topic(i)
        ))
        lay.addLayout(head)

        topic_widgets = []
        for ti, topic in enumerate(phase["topics"]):
            lay.addWidget(self._topic_widget(pi, ti, topic, topic_widgets))
        self._phase_widgets.append(
            {"name": name, "goal": goal, "order": order, "topics": topic_widgets}
        )
        return box

    def _topic_widget(self, pi, ti, topic, out_list) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 2, 0, 2)
        lay.setSpacing(2)
        row = QHBoxLayout()
        name = QLineEdit(topic["name"])
        name.setPlaceholderText("知识点名称")
        row.addWidget(name, 3)
        row.addWidget(QLabel("分钟"))
        minutes = QSpinBox()
        minutes.setRange(MIN_TOPIC_MINUTES, MAX_TOPIC_MINUTES)
        minutes.setValue(int(topic["estimated_minutes"]))
        row.addWidget(minutes)
        row.addWidget(QLabel("优先级"))
        priority = QSpinBox()
        priority.setRange(1, 5)
        priority.setValue(int(topic["priority"]))
        row.addWidget(priority)
        row.addWidget(QLabel("顺序"))
        order = QSpinBox()
        order.setRange(1, 99)
        order.setValue(int(topic["order"]))
        row.addWidget(order)
        row.addWidget(_secondary(
            "删除", lambda _=False, p=pi, t=ti: self._on_delete_topic(p, t)
        ))
        lay.addLayout(row)
        desc = QPlainTextEdit(topic["description"])
        desc.setFixedHeight(56)
        desc.setPlaceholderText("学习目标 / 核心概念 / 最小实践 / 完成标准")
        lay.addWidget(desc)
        out_list.append({
            "name": name, "description": desc, "minutes": minutes,
            "priority": priority, "order": order,
        })
        return box

    # ---------- 结构操作 ----------

    def _on_add_phase(self) -> None:
        self._collect()
        self._model.append({
            "name": f"阶段 {len(self._model) + 1}",
            "goal": "",
            "order": len(self._model) + 1,
            "topics": [{
                "name": "新知识点", "description": "学习目标：\n核心概念：\n"
                "最小实践：\n完成标准：",
                "estimated_minutes": 30, "priority": 3, "order": 1,
            }],
        })
        self._render()

    def _on_delete_phase(self, index: int) -> None:
        self._collect()
        if len(self._model) <= 1:
            show_warning(self, "至少保留一个阶段。")
            return
        self._model.pop(index)
        self._render()

    def _on_add_topic(self, phase_index: int) -> None:
        self._collect()
        topics = self._model[phase_index]["topics"]
        topics.append({
            "name": "新知识点",
            "description": "学习目标：\n核心概念：\n最小实践：\n完成标准：",
            "estimated_minutes": 30, "priority": 3, "order": len(topics) + 1,
        })
        self._render()

    def _on_delete_topic(self, phase_index: int, topic_index: int) -> None:
        self._collect()
        topics = self._model[phase_index]["topics"]
        if len(topics) <= 1:
            show_warning(self, "每个阶段至少保留一个知识点。")
            return
        topics.pop(topic_index)
        self._render()

    # ---------- 确认 ----------

    def _on_confirm(self) -> None:
        self._collect()
        if not self.plan_name_edit.text().strip():
            show_warning(self, "计划名称不能为空。")
            return
        for p in self._model:
            if not p["name"].strip():
                show_warning(self, "阶段名称不能为空。")
                return
            if not p["topics"]:
                show_warning(self, "每个阶段至少需要一个知识点。")
                return
            for t in p["topics"]:
                if not t["name"].strip():
                    show_warning(self, "知识点名称不能为空。")
                    return
                if not t["description"].strip():
                    show_warning(self, f"知识点「{t['name']}」缺少可执行说明。")
                    return
        self.replace_empty = bool(
            self.replace_check is not None and self.replace_check.isChecked()
        )
        self.accept()


class SkillPickerDialog(QDialog):
    """从既有技能中选择一个关联到路线（不创建新 skill）。"""

    def __init__(self, skill_names, parent=None):
        super().__init__(parent)
        self.setWindowTitle("关联已有技能")
        self.setModal(True)
        self.resize(420, 180)
        self.selected: str | None = None
        root = QVBoxLayout(self)
        root.setSpacing(8)
        title = QLabel("选择要关联到当前路线的技能（不新建技能）")
        title.setObjectName("SectionTitle")
        root.addWidget(title)
        root.addWidget(QLabel("已有关联的技能不会重复出现。"))
        root.addWidget(QLabel("技能"))
        self.combo = QComboBox()
        for n in skill_names:
            self.combo.addItem(n, n)
        root.addWidget(self.combo)
        root.addStretch()
        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(_secondary("取消", self.reject))
        btns.addWidget(_secondary("关联", self._on_ok))
        root.addLayout(btns)

    def _on_ok(self) -> None:
        self.selected = self.combo.currentData()
        self.accept()
