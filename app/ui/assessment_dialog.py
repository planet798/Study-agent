"""验收对话框（Phase 7）。

流程：
- 展示 Phase 3C 生成的验收题；
- 用户逐题作答并提交；
- 后台调用 Phase 3D 的 assessment_service.submit_answers（AI 判题）；
- 展示 AI 判题结果（掌握度估计、每题判定、薄弱点）。

安全：绝不提供用户自评“掌握度”的控件；所有结果来自真实作答 + AI 判定。
"""

from __future__ import annotations

import json

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .ai_worker import AssessmentWorker, run_submit_answers


class AssessmentDialog(QDialog):
    # 验收成功判题后发出（task_id），供上层完成复习任务等
    assessment_completed = Signal(int)

    def __init__(self, assessment_service, attempt, today, parent=None,
                 service_factory=None, db_path=None):
        super().__init__(parent)
        self._service = assessment_service
        self._attempt = attempt
        self._today = today
        # 后台判题只传 db_path + 工厂（worker 内自建连接），不跨线程复用主线程连接
        self._service_factory = service_factory or (lambda conn: self._service)
        self._db_path = db_path
        self._answer_edits: list[QPlainTextEdit] = []

        self.setWindowTitle("学习验收")
        self.setModal(False)
        self.resize(620, 640)

        root = QVBoxLayout(self)
        self.title_label = QLabel("学习验收")
        self.title_label.setObjectName("SectionTitle")
        root.addWidget(self.title_label)

        self.status_label = QLabel("")
        self.status_label.setObjectName("TaskMeta")
        root.addWidget(self.status_label)

        self.error_label = QLabel("")
        self.error_label.setObjectName("QErrorMessage")
        self.error_label.setStyleSheet("color: #e74c3c;")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)

        # 题目区
        self.questions_area = QScrollArea()
        self.questions_area.setWidgetResizable(True)
        self.questions_container = QWidget()
        self.questions_layout = QVBoxLayout(self.questions_container)
        self.questions_area.setWidget(self.questions_container)
        root.addWidget(self.questions_area, stretch=1)

        # 结果区
        self.result_container = QWidget()
        self.result_container.setVisible(False)
        rlay = QVBoxLayout(self.result_container)
        self.result_mastery = QLabel("")
        self.result_level = QLabel("")
        self.result_verdicts = QLabel("")
        self.result_verdicts.setWordWrap(True)
        self.result_weak = QLabel("")
        self.result_weak.setWordWrap(True)
        for w in (
            self.result_mastery,
            self.result_level,
            self.result_verdicts,
            self.result_weak,
        ):
            w.setObjectName("TaskMeta")
            rlay.addWidget(w)
        root.addWidget(self.result_container)

        buttons = QHBoxLayout()
        self.submit_btn = QPushButton("提交答案")
        self.submit_btn.setObjectName("PrimaryButton")
        self.submit_btn.clicked.connect(self._on_submit)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        buttons.addStretch()
        buttons.addWidget(close_btn)
        buttons.addWidget(self.submit_btn)
        root.addLayout(buttons)

        self._render_questions(attempt)

    # ---------- 题目渲染 ----------

    def _parse_questions(self, attempt) -> list[dict]:
        questions = attempt.get("questions")
        if questions:
            out = []
            for q in questions:
                if isinstance(q, dict):
                    out.append(q)
                else:
                    out.append({
                        "question": getattr(q, "question", ""),
                        "type": getattr(q, "type", ""),
                    })
            return out
        try:
            data = json.loads(attempt.get("questions_json") or "[]")
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def _render_questions(self, attempt) -> None:
        self._attempt = attempt
        questions = self._parse_questions(attempt)

        while self.questions_layout.count():
            item = self.questions_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._answer_edits.clear()

        if not questions:
            self.status_label.setText("该验收没有可用题目。")
            return

        self.status_label.setText(f"共 {len(questions)} 道题，请逐题作答后提交。")
        for i, q in enumerate(questions):
            qtype = q.get("type", "")
            qtext = q.get("question", "")
            qlab = QLabel(f"{i + 1}. [{qtype}] {qtext}")
            qlab.setWordWrap(True)
            edit = QPlainTextEdit()
            edit.setPlaceholderText("请输入你的作答…")
            self.questions_layout.addWidget(qlab)
            self.questions_layout.addWidget(edit)
            self._answer_edits.append(edit)
        self.questions_layout.addStretch()

    # ---------- 提交流程 ----------

    def _set_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.error_label.setVisible(True)

    def _on_submit(self) -> None:
        answers = [e.toPlainText().strip() for e in self._answer_edits]
        if any(not a for a in answers):
            self._set_error("请完成所有题目后再提交")
            return
        self.error_label.setVisible(False)
        self.submit_btn.setEnabled(False)
        self.status_label.setText("正在判题…")
        try:
            worker = AssessmentWorker(
                run_submit_answers,
                self._service_factory,
                db_path=self._db_path,
                args=(self._attempt["id"], answers),
                kwargs={"today": self._today},
                parent=self,
            )
            worker.succeeded.connect(self._on_judged)
            worker.failed.connect(self._on_failed)
            worker.finished.connect(lambda w=worker: w.deleteLater())
            worker.start()
        except Exception as e:  # noqa: BLE001
            self._on_failed(str(e))

    def _on_judged(self, attempt) -> None:
        self.submit_btn.setEnabled(True)
        self._attempt = attempt
        if attempt.get("judge_status") != "judged":
            self.status_label.setText("判题失败（已保存答案，可稍后重试）")
            err = attempt.get("judge_error") or ""
            self._set_error(f"AI 判题失败：{err}" if err else "AI 判题失败")
            return
        task_id = attempt.get("task_id")
        if task_id:
            self.assessment_completed.emit(task_id)
        self._show_result(attempt)

    def _on_failed(self, msg: str) -> None:
        self.submit_btn.setEnabled(True)
        self.status_label.setText("判题失败（已保存答案，可稍后重试）")
        self._set_error(f"AI 调用失败：{msg}")

    def _show_result(self, attempt) -> None:
        self.result_container.setVisible(True)
        mastery = attempt.get("mastery_estimate")
        if mastery is None:
            self.result_mastery.setText("掌握度估计：—")
        else:
            self.result_mastery.setText(
                f"掌握度估计（AI）：{int(round(float(mastery) * 100))}%"
            )
        self.result_level.setText(f"整体结果：{attempt.get('result_level') or '—'}")
        try:
            verdicts = json.loads(
                attempt.get("ai_result_json") or "{}"
            ).get("questions", [])
        except json.JSONDecodeError:
            verdicts = []
        if verdicts:
            lines = [
                f"第 {v.get('question_index', 0) + 1} 题：{v.get('verdict', '—')}"
                f" — {v.get('reason', '')}"
                for v in verdicts
            ]
            self.result_verdicts.setText("每题判定：\n" + "\n".join(lines))
        else:
            self.result_verdicts.setText("每题判定：—")
        try:
            weak = json.loads(attempt.get("weak_points_json") or "[]")
        except json.JSONDecodeError:
            weak = []
        self.result_weak.setText(
            f"薄弱点：{'、'.join(weak) if weak else '未发现明显薄弱点'}"
        )
        self.status_label.setText("验收完成")
