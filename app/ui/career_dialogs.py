"""Phase E UI：职业/技能/JD/简历相关的对话框与展示辅助。

职责：只负责展示与用户输入，把所有业务交给 JdService / SkillService /
LearningOutcomeService / NotesService；本模块不重算任何优先级。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .styles import apply_secondary_button_text
from .dialogs import show_warning


def format_impact(impact: dict) -> str:
    """把 JdService.preview_impact 渲染成 human-readable 文本（只展示，不算）。"""
    parsed = impact.get("parsed") or {}
    lines = [
        f"解析方法：{parsed.get('method') or '—'}"
        f" | 方向：{parsed.get('direction') or '—'}"
        f" | 实习：{'是' if parsed.get('intern') else '否'}",
        "",
    ]
    must = parsed.get("must") or []
    plus = parsed.get("plus") or []
    if must:
        lines.append("必须技能：" + "、".join(must))
    else:
        lines.append("必须技能：（无匹配现有技能）")
    if plus:
        lines.append("加分技能：" + "、".join(plus))
    affected = impact.get("affected_skills") or []
    if affected:
        lines.append("")
        lines.append("对技能优先级的影响：")
        for e in affected:
            lines.append(
                f"  - {e['explanation']}；priority "
                f"{e['priority_before']:.4f} -> {e['priority_after']:.4f}"
            )
    else:
        lines.append("")
        lines.append("对技能优先级的影响：（不影响现有技能）")
    gap = impact.get("jd_gap_skills") or []
    if gap:
        lines.append("")
        lines.append("JD 缺口（需学习）：" + "、".join(
            e["skill"] for e in gap))
    blocked = impact.get("prerequisite_blocked") or []
    if blocked:
        lines.append("前置未满足（不越级安排）：" + "、".join(
            e["skill"] for e in blocked))
    focus = impact.get("weekly_focus") or []
    if focus:
        lines.append("")
        lines.append("未来 1~2 周建议重点关注：" + "、".join(
            e["skill"] for e in focus))
    not_rush = impact.get("not_rush") or []
    if not_rush:
        lines.append("不建议抢占主线（低优先级）：" + "、".join(not_rush))
    return "\n".join(lines)


class JdInputDialog(QDialog):
    """添加 JD：粘贴文本 / 选文件 -> 分析预览 -> 正式保存。

    分析（dry-run 只读，不写库）；保存才真正入库并刷新技能频率/优先级。
    """

    def __init__(self, jd_service, parent: QWidget | None = None):
        super().__init__(parent)
        self.jd_service = jd_service
        self.setWindowTitle("添加 JD")
        self.setModal(True)
        self.resize(620, 560)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel("添加招聘 JD（动态输入，仅影响短期技能优先级）")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        row0 = QHBoxLayout()
        self.company_edit = QLineEdit()
        self.company_edit.setPlaceholderText("公司（可选）")
        self.role_edit = QLineEdit()
        self.role_edit.setPlaceholderText("岗位（可选）")
        row0.addWidget(QLabel("公司："))
        row0.addWidget(self.company_edit, 1)
        row0.addWidget(QLabel("岗位："))
        row0.addWidget(self.role_edit, 1)
        layout.addLayout(row0)

        file_btn = QPushButton("选择 JD 文件…")
        file_btn.setObjectName("PostponeButton")
        file_btn.clicked.connect(self._pick_file)
        layout.addWidget(file_btn)

        self.jd_edit = QPlainTextEdit()
        self.jd_edit.setPlaceholderText("粘贴 JD 原文…")
        layout.addWidget(self.jd_edit, 1)

        btn_row = QHBoxLayout()
        analyze_btn = QPushButton("分析 / 预览")
        analyze_btn.setObjectName("PrimaryButton")
        analyze_btn.clicked.connect(self._analyze)
        save_btn = QPushButton("保存 JD")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(analyze_btn)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self.result_edit = QPlainTextEdit()
        self.result_edit.setReadOnly(True)
        self.result_edit.setPlaceholderText("分析结果将显示在这里（dry-run，不写库）")
        layout.addWidget(self.result_edit, 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存并关闭")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("关闭")
        self.buttons.accepted.connect(self._accept_saved)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(
            QDialogButtonBox.StandardButton.Ok
        ).setEnabled(False)
        layout.addWidget(self.buttons)

    # ---------- 交互 ----------

    def jd_text(self) -> str:
        return self.jd_edit.toPlainText().strip()

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 JD 文件", "", "Text (*.txt *.md *.json);;All (*)"
        )
        if not path:
            return
        try:
            from pathlib import Path

            self.jd_edit.setPlainText(Path(path).read_text(encoding="utf-8"))
        except OSError as e:
            show_warning(self, f"读取文件失败：{e}")

    def _render(self, text: str) -> None:
        self.result_edit.setPlainText(text)

    def _analyze(self) -> None:
        text = self.jd_text()
        if not text:
            self._render("请先粘贴 JD 文本或选择 JD 文件。")
            return
        try:
            impact = self.jd_service.preview_impact(text, use_ai=True)
        except Exception as e:  # noqa: BLE001 - 分析失败不崩溃
            self._render(f"分析失败：{e}")
            return
        self._render(format_impact(impact))
        self.buttons.button(
            QDialogButtonBox.StandardButton.Ok
        ).setEnabled(True)

    def _save(self) -> None:
        text = self.jd_text()
        if not text:
            show_warning(self, "JD 文本为空，无法保存。")
            return
        try:
            self.jd_service.add_jd(
                text, company=self.company_edit.text().strip(),
                title=self.role_edit.text().strip(), use_ai=True,
            )
        except Exception as e:  # noqa: BLE001 - 保存失败不崩溃
            show_warning(self, f"保存失败：{e}")
            return
        self.result_edit.setPlainText(
            "已保存到 jds，并更新了技能 JD 频率与优先级（只影响近期优先级）。"
        )

    def _accept_saved(self) -> None:
        if self.jd_text():
            self.accept()


class JdDetailDialog(QDialog):
    """展示单条 JD 的解析与影响详情。"""

    def __init__(self, jd_service, jd: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.jd_service = jd_service
        self.jd = jd
        self.setWindowTitle(f"JD 详情：{jd.get('title') or jd.get('company') or jd['id']}")
        self.setModal(True)
        self.resize(600, 520)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        info = QLabel(
            f"公司：{jd.get('company') or '—'} ｜ 岗位：{jd.get('title') or '—'}"
            f" ｜ 方向：{jd.get('direction') or '—'}"
            f" ｜ 上传：{jd.get('uploaded_at') or '—'}"
        )
        info.setObjectName("TaskMeta")
        layout.addWidget(info)

        text = QPlainTextEdit()
        text.setReadOnly(True)
        # 详情 = 解析结构 + 优先级影响（来自 JdService，不是 UI 计算）
        parsed = jd.get("parsed") or {}
        impact_txt = ""
        try:
            impact = self.jd_service.preview_impact(jd.get("raw_text") or "")
            impact_txt = format_impact(impact)
        except Exception:  # noqa: BLE001 - 详情渲染失败不崩溃
            impact_txt = "（影响分析暂不可用）"
        body = (
            f"岗位方向：{parsed.get('direction') or '—'}\n"
            f"实习：{'是' if parsed.get('intern') else '否'}\n"
            f"必须技能：{'、'.join(parsed.get('must') or []) or '（无）'}\n"
            f"加分技能：{'、'.join(parsed.get('plus') or []) or '（无）'}\n\n"
            "--- 对当前技能优先级的影响 ---\n" + impact_txt
        )
        text.setPlainText(body)
        layout.addWidget(text, 1)

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("PrimaryButton")
        close_btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close_btn)
        layout.addLayout(row)


class ResumeMaterialDialog(QDialog):
    """展示简历素材（bullets + 关键词），只显示真实事实。"""

    def __init__(self, outcome_service, outcomes, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("简历素材")
        self.setModal(True)
        self.resize(600, 460)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        try:
            mat = outcome_service.build_resume_material(outcomes or [])
        except Exception:  # noqa: BLE001
            mat = {"resume_keywords": [], "candidate_bullets": [],
                   "technical_summary": ""}

        text = QPlainTextEdit()
        text.setReadOnly(True)
        kw = "、".join(mat.get("resume_keywords") or [])
        bullets = mat.get("candidate_bullets") or []
        summary = (mat.get("technical_summary") or "").strip()
        lines = []
        if summary:
            lines.append(f"概述：{summary}")
        if kw:
            lines.append("")
            lines.append("关键词：" + kw)
        lines.append("")
        lines.append("候选描述（仅含真实事实）：")
        if bullets:
            for b in bullets:
                lines.append(f"  - {b}")
        else:
            lines.append("  暂无可直接用于简历的项目/实验类成果。")
        text.setPlainText("\n".join(lines))
        layout.addWidget(text, 1)

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("PrimaryButton")
        close_btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close_btn)
        layout.addLayout(row)
