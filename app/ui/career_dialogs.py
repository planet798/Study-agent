"""Phase E UI：职业/技能/JD/简历相关的对话框与展示辅助。

职责：只负责展示与用户输入，把所有业务交给 JdService / SkillService /
LearningOutcomeService / NotesService；本模块不重算任何优先级。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
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


def format_summary_preview(preview: dict) -> str:
    """把 JdSummaryService.preview_summary 渲染成文本（UI 不算任何统计）。"""
    lines: list[str] = []
    if preview.get("errors"):
        lines.append("数据错误（不会保存）：")
        for e in preview["errors"]:
            lines.append(f"  ! {e}")
        lines.append("")
    sample = preview.get("sample_count") or 0
    lines.append(f"样本岗位数：{sample}")
    lines.append("")
    matched = preview.get("matched") or []
    if matched:
        lines.append("已匹配技能：")
        for r in matched:
            lines.append(
                f"  {r['name']}    {r['mention_count']} / {sample}    "
                f"{r['frequency'] * 100:.1f}%"
            )
    else:
        lines.append("本次汇总暂无可映射技能")
    unmatched = preview.get("unmatched") or []
    if unmatched:
        lines.append("")
        lines.append("未匹配技能（已保存原始写法，将作为 JD 新技能候选）：")
        for r in unmatched:
            lines.append(
                f"  {r['raw_skill_name']}    {r['mention_count']} / {sample}"
            )
        lines.append(
            "这些技术暂未映射到现有技能；达到阈值后会在首页进入"
            "“JD 新技能候选”，确认后才加入技能体系。"
        )
    return "\n".join(lines)


class JdCandidateAcceptDialog(QDialog):
    """确认把 JD 新技能候选加入正式技能体系（用户确认，不自动创建）。"""

    def __init__(self, candidate: dict, existing_names=None, parent=None):
        super().__init__(parent)
        self.candidate = candidate
        self.existing_names = list(existing_names or [])
        self.result_name = ""
        self.result_tier = "A"
        self.result_linked_skill = None
        self.setWindowTitle("加入技能体系")
        self.setModal(True)
        self.resize(520, 300)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        title = QLabel("把 JD 新技能候选加入正式技能体系")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        source = QLabel(
            f"JD 原始名称：{candidate.get('canonical_name') or '—'}"
            f"（近30天 {int(candidate.get('mention_count_30d') or 0)} 次 · "
            f"{float(candidate.get('frequency_30d') or 0) * 100:.0f}%）"
        )
        source.setObjectName("TaskMeta")
        source.setWordWrap(True)
        layout.addWidget(source)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("建议正式名称："))
        self.name_edit = QLineEdit(
            candidate.get("suggested_name")
            or candidate.get("canonical_name")
            or ""
        )
        name_row.addWidget(self.name_edit, 1)
        layout.addLayout(name_row)

        tier_row = QHBoxLayout()
        tier_row.addWidget(QLabel("Tier："))
        self.tier_combo = QComboBox()
        for t in ("S", "A", "B", "C"):
            self.tier_combo.addItem(t, t)
        self.tier_combo.setCurrentIndex(1)  # 默认 A
        tier_row.addWidget(self.tier_combo)
        tier_row.addWidget(QLabel("关联已有技能（可选）："))
        self.link_combo = QComboBox()
        self.link_combo.addItem("（新建技能）", None)
        for n in self.existing_names:
            self.link_combo.addItem(n, n)
        tier_row.addWidget(self.link_combo, 1)
        layout.addLayout(tier_row)

        hint = QLabel(
            "加入后：初始状态为 not_started；不自动创建任务 / 验收 / 掌握度。"
            "若该技能尚无可对应课程，会被标记为“课程缺口”。"
        )
        hint.setObjectName("TaskMeta")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #e74c3c;")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("确认加入")
        ok.setObjectName("SecondaryButton")
        apply_secondary_button_text(ok)
        ok.clicked.connect(self._confirm)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        layout.addLayout(btns)

    def _confirm(self) -> None:
        linked = self.link_combo.currentData()
        if linked:
            self.result_linked_skill = linked
            self.result_name = linked
            self.accept()
            return
        name = self.name_edit.text().strip()
        if not name:
            self.error_label.setText("正式名称不能为空")
            self.error_label.setVisible(True)
            return
        self.result_linked_skill = None
        self.result_name = name
        self.result_tier = self.tier_combo.currentData() or "A"
        self.accept()


class JdSummaryInputDialog(QDialog):
    """添加今日 JD 技术汇总：日期 + 目标 + 样本数 + 汇总文本 → 预览 → 保存。

    所有解析 / alias / 校验 / 频率都由 JdSummaryService 完成，UI 不做任何统计。
    """

    def __init__(self, jd_summary_service, today: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.service = jd_summary_service
        self.saved: dict | None = None
        self._last_valid: dict | None = None
        self.setWindowTitle("添加今日 JD 技术汇总")
        self.setModal(True)
        self.resize(660, 640)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel("添加今日 JD 技术汇总（人工统计，仅作为市场样本）")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        row = QHBoxLayout()
        self.date_edit = QLineEdit(today)
        self.date_edit.setPlaceholderText("YYYY-MM-DD")
        self.target_combo = QComboBox()
        for label, value in (("实习", "internship"), ("校招", "campus"),
                             ("社招", "fulltime")):
            self.target_combo.addItem(label, value)
        self.sample_spin = QSpinBox()
        self.sample_spin.setRange(1, 100000)
        self.sample_spin.setValue(1)
        row.addWidget(QLabel("日期："))
        row.addWidget(self.date_edit, 1)
        row.addWidget(QLabel("目标："))
        row.addWidget(self.target_combo)
        row.addWidget(QLabel("样本岗位数："))
        row.addWidget(self.sample_spin)
        layout.addLayout(row)

        self.notice = QLabel("")
        self.notice.setObjectName("TaskMeta")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(
            "每行一个技能，例如：\nPython 13\nPyTorch 11\nEmbedding 9\n"
            "也可用 Python: 13 / Python：13 / HF 4 / Python must=10 plus=2"
        )
        layout.addWidget(self.text_edit, 2)

        btn_row = QHBoxLayout()
        self.preview_btn = QPushButton("分析预览")
        self.preview_btn.setObjectName("SecondaryButton")
        apply_secondary_button_text(self.preview_btn)
        self.preview_btn.clicked.connect(self._preview)
        self.save_btn = QPushButton("确认保存")
        self.save_btn.setObjectName("SecondaryButton")
        apply_secondary_button_text(self.save_btn)
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self.preview_btn)
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self.preview_edit = QPlainTextEdit()
        self.preview_edit.setReadOnly(True)
        self.preview_edit.setPlaceholderText("点击“分析预览”查看结果（不写库）")
        layout.addWidget(self.preview_edit, 2)

        self._load_existing(today)

    # ---------- 同日已有数据 ----------

    def _target(self) -> str:
        return self.target_combo.currentData() or "internship"

    def _load_existing(self, today: str) -> None:
        if self.service is None:
            return
        try:
            summary = self.service.get_summary(today, self._target())
        except Exception:  # noqa: BLE001
            summary = None
        if summary:
            self.sample_spin.setValue(int(summary.get("sample_count") or 1))
            self.text_edit.setPlainText(summary.get("raw_text") or "")
            self.notice.setText(
                "今天已有一份 JD 汇总，保存后将更新原记录，不会重复累计。"
            )

    # ---------- 预览 ----------

    def _preview(self) -> dict | None:
        if self.service is None:
            self.preview_edit.setPlainText("JD 汇总服务不可用")
            return None
        text = self.text_edit.toPlainText()
        try:
            preview = self.service.preview_summary(
                text, self.sample_spin.value(), self._target(),
                self.date_edit.text().strip(),
            )
        except Exception as e:  # noqa: BLE001 - 不崩溃
            self.preview_edit.setPlainText(f"分析失败：{e}")
            self.save_btn.setEnabled(False)
            self._last_valid = None
            return None
        self.preview_edit.setPlainText(format_summary_preview(preview))
        self._last_valid = preview if preview.get("valid") else None
        self.save_btn.setEnabled(bool(self._last_valid))
        return preview

    # ---------- 保存 ----------

    def _save(self) -> None:
        preview = self._preview()  # 始终以当前输入重新校验
        if not preview or not preview.get("valid"):
            return
        date = self.date_edit.text().strip()
        try:
            row = self.service.save_summary(
                date, self.text_edit.toPlainText(), self.sample_spin.value(),
                self._target(),
            )
        except Exception as e:  # noqa: BLE001 - 保存失败不崩溃
            self.preview_edit.setPlainText(f"保存失败：{e}")
            return
        self.saved = {
            "summary_date": row["summary_date"],
            "sample_count": row["sample_count"],
            "target_type": row["target_type"],
        }
        self.accept()


class JdHistoryDialog(QDialog):
    """历史 individual JD：列表 + 查看详情（复用 JdDetailDialog）+ 兼容“添加 JD”。"""

    def __init__(self, jd_service, parent: QWidget | None = None):
        super().__init__(parent)
        self.jd_service = jd_service
        self.setWindowTitle("历史 JD")
        self.setModal(True)
        self.resize(680, 520)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        title = QLabel("历史 JD（单条记录，保留作为历史证据）")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        try:
            jds = self.jd_service.jd_repo.list_all() if jd_service else []
        except Exception:  # noqa: BLE001
            jds = []

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 0, 0)
        if not jds:
            empty = QLabel("暂无历史 JD")
            empty.setObjectName("EmptyHint")
            col.addWidget(empty)
        for jd in jds:
            parsed = jd.get("parsed") or {}
            row_w = QWidget()
            row = QHBoxLayout(row_w)
            row.setContentsMargins(0, 0, 0, 0)
            info = QLabel(
                f"{jd.get('uploaded_at') or '—'}｜{jd.get('company') or '—'}｜"
                f"{jd.get('title') or '—'}｜{jd.get('direction') or '—'}｜"
                f"{'实习' if parsed.get('intern') else '全职'}"
            )
            info.setObjectName("TaskMeta")
            btn = QPushButton("查看详情")
            btn.setObjectName("SecondaryButton")
            apply_secondary_button_text(btn)
            btn.clicked.connect(lambda _=False, jd=jd: self._show_detail(jd))
            row.addWidget(info, 1)
            row.addWidget(btn)
            col.addWidget(row_w)
        col.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("添加 JD（兼容入口）")
        add_btn.setObjectName("SecondaryButton")
        apply_secondary_button_text(add_btn)
        add_btn.clicked.connect(self._add_jd)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("SecondaryButton")
        apply_secondary_button_text(close_btn)
        close_btn.clicked.connect(self.accept)
        btn_row.addStretch()
        btn_row.addWidget(add_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _show_detail(self, jd: dict) -> None:
        JdDetailDialog(self.jd_service, jd, parent=self).exec()

    def _add_jd(self) -> None:
        JdInputDialog(self.jd_service, parent=self).exec()
