"""Obsidian 每日学习笔记导出（Phase D）。

- export_daily_note(date, output_dir) -> 写 YYYY-MM-DD.md
- 只记录系统真实知道的事实（task / topic / knowledge_point / assessment /
  learning_outcome / 用户主动记录），绝不伪造学习内容、验收结果或资源学习行为。
- 信息不足的部分明确写“> 今日暂无该部分记录”。
- 幂等：同一天重复导出为覆盖同一文件、内容稳定，不重复追加。
（本服务不写学习路线 / 不建任务 / 不改 Planner）
"""

from __future__ import annotations

import json
from pathlib import Path

from ..utils.date_utils import add_days

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "obsidian"

_NONE = "> 今日暂无该部分记录"


def _latest_judged_attempt(assessment_repo, kp_id: int) -> dict | None:
    if assessment_repo is None:
        return None
    rows = assessment_repo.list_attempts_for_knowledge_point(kp_id)
    judged = [a for a in rows if a.get("judge_status") == "judged"]
    if not judged:
        return None
    judged.sort(key=lambda a: a.get("id") or 0)
    return judged[-1]


def _weak_points(raw) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


class NotesService:
    """把某一天的真实学习状态渲染为独立 Markdown 笔记。"""

    def __init__(
        self,
        repo=None,
        study_plan_service=None,
        outcome_service=None,
        assessment_repo=None,
        jd_service=None,
    ):
        self.repo = repo
        self.study_plan_service = study_plan_service
        self.outcome_service = outcome_service
        self.assessment_repo = assessment_repo
        self.jd_service = jd_service

    # ================= 渲染 =================

    def build_daily_note(self, date: str) -> str:
        lines = [f"# {date} 学习笔记", ""]
        lines += self._section_goals(date)
        lines += self._section_new_knowledge(date)
        lines += self._section_review(date)
        lines += self._section_extra(date)
        lines += self._section_exploration(date)
        lines += self._section_outcomes(date)
        lines += self._section_weak_points(date)
        lines += self._section_resume_material(date)
        lines += self._section_tomorrow(date)
        return "\n".join(lines).rstrip() + "\n"

    # ---------- 各节 ----------

    def _section_goals(self, date: str) -> list[str]:
        lines = ["## 今日学习目标", ""]
        goal = ""
        if self.study_plan_service is not None:
            phase = self.study_plan_service.get_current_phase(date)
            if phase is not None:
                goal = (phase.goals or "").strip()
                lines.append(f"- 当前阶段：{phase.name}")
        if goal:
            lines.append(f"- 阶段目标：{goal}")
        else:
            lines.append(_NONE)
        return lines + [""]

    def _section_new_knowledge(self, date: str) -> list[str]:
        lines = ["## 今日新知识", ""]
        tasks = []
        if self.repo is not None:
            tasks = [t for t in self.repo.list_by_date(date)
                     if t.task_type == "new"]
        if not tasks:
            return lines + [_NONE, ""]
        for t in tasks:
            lines.append(f"### {t.title}")
            if t.description:
                lines.append(f"- 描述：{t.description}")
            if t.knowledge_point_id is not None:
                kp_name = t.title
                if self.assessment_repo is not None:
                    kp = self.assessment_repo.get_knowledge_point(
                        t.knowledge_point_id
                    )
                    if kp:
                        kp_name = kp["name"] or kp_name
                lines.append(f"- 相关知识点：{kp_name}")
            status_txt = (
                "已完成" if t.status == "done"
                else "已移除（今日不再执行）" if t.status == "cancelled"
                else "进行中/待完成" if t.status == "active"
                else "未完成"
            )
            lines.append(f"- 完成情况：{status_txt}")
            lines.append("")
        return lines

    def _section_review(self, date: str) -> list[str]:
        lines = ["## 今日复习", ""]
        reviews = []
        if self.repo is not None:
            reviews = [t for t in self.repo.list_by_date(date)
                       if t.task_type == "review"]
        if not reviews:
            return lines + [_NONE, ""]
        for t in reviews:
            lines.append(f"### {t.title}")
            kp_id = t.knowledge_point_id
            if kp_id is not None and self.assessment_repo is not None:
                attempt = _latest_judged_attempt(self.assessment_repo, kp_id)
                kp = self.assessment_repo.get_knowledge_point(kp_id)
                if attempt is not None:
                    mastery = attempt.get("mastery_estimate")
                    m_txt = (
                        f"mastery 估计 {float(mastery):.2f}"
                        if mastery is not None else "暂无 mastery 估计"
                    )
                    lines.append(f"- 验收结果：{attempt.get('result_level') or '—'}")
                    lines.append(f"- {m_txt}（AI 估计，非绝对事实）")
                    weak = _weak_points(attempt.get("weak_points_json"))
                    if weak:
                        lines.append("- 薄弱点：" + "、".join(weak))
                    nxt = (kp or {}).get("next_review_date")
                    if nxt:
                        lines.append(f"- 下一次复习日期：{nxt}")
            else:
                lines.append("- 暂无关联验收结果")
            lines.append("")
        return lines

    def _section_extra(self, date: str) -> list[str]:
        lines = ["## 今日额外学习", ""]
        extras = []
        if self.repo is not None:
            extras = [t for t in self.repo.list_by_date(date)
                      if t.task_type == "extra"]
        if not extras:
            return lines + [_NONE, ""]
        for t in extras:
            status_txt = (
                "已完成" if t.status == "done"
                else "已移除" if t.status == "cancelled"
                else "未完成"
            )
            lines.append(f"- {t.title}（{status_txt}）")
        return lines + [""]

    def _section_exploration(self, date: str) -> list[str]:
        lines = ["## 课外探索", ""]
        lines.append(
            "> 系统目前只记录“推荐了哪些资源”，不掌握用户是否真正学习；"
            "因此这里只作资源记录，不声称“已学习”。"
        )
        lines.append(_NONE)
        return lines + [""]

    def _section_outcomes(self, date: str) -> list[str]:
        lines = ["## 学习成果", ""]
        outcomes = []
        if self.outcome_service is not None:
            outcomes = self.outcome_service.list_by_date(date)
        if not outcomes:
            return lines + [_NONE, ""]
        for o in outcomes:
            lines.append(f"- **{o['title']}**（{o['kind']}）")
            if o.get("content"):
                lines.append(f"  - {o['content']}")
            if o.get("tech_stack"):
                lines.append("  - 技术栈：" + "、".join(o["tech_stack"]))
            if o.get("metrics"):
                lines.append("  - 指标：" + "、".join(
                    f"{k}={v}" for k, v in o["metrics"].items()))
            if o.get("dataset"):
                lines.append(f"  - 数据集：{o['dataset']}")
            if o.get("git_commit"):
                lines.append(f"  - commit：{o['git_commit']}")
            if o.get("github_url"):
                lines.append(f"  - 代码：{o['github_url']}")
        return lines + [""]

    def _section_weak_points(self, date: str) -> list[str]:
        lines = ["## 薄弱点", ""]
        weak: list[str] = []
        if self.repo is not None and self.assessment_repo is not None:
            for t in self.repo.list_by_date(date):
                if t.task_type != "review" or t.knowledge_point_id is None:
                    continue
                attempt = _latest_judged_attempt(
                    self.assessment_repo, t.knowledge_point_id
                )
                for w in _weak_points(attempt and attempt.get("weak_points_json")):
                    if w and w not in weak:
                        weak.append(w)
        if not weak:
            return lines + [_NONE, ""]
        return lines + [f"- {w}" for w in weak] + [""]

    def _section_resume_material(self, date: str) -> list[str]:
        lines = ["## 简历素材", ""]
        if self.outcome_service is None:
            return lines + [_NONE, ""]
        day_outcomes = self.outcome_service.list_by_date(date)
        mat = self.outcome_service.build_resume_material(day_outcomes)
        bullets = [b for b in mat.get("candidate_bullets", [])
                   if b and "尚无已完成的" not in b]
        if not bullets:
            return lines + [_NONE, ""]
        if mat.get("resume_keywords"):
            lines.append("- 关键词：" + "、".join(mat["resume_keywords"]))
        for b in bullets:
            lines.append(f"- {b}")
        return lines + [""]

    def _section_tomorrow(self, date: str) -> list[str]:
        lines = ["## 明日建议", ""]
        lines.append("（仅建议，不修改明日 Planner 安排）")
        if self.jd_service is not None:
            try:
                preview = self.jd_service.preview_weekly_priorities(
                    date, days=1, top_each_day=3
                )
                skills = preview["daily_focus"][0]["skills"]
                if skills:
                    lines.append(
                        "建议明日重点关注：" + "、".join(skills)
                        + "（依据技能优先级预览）"
                    )
                else:
                    lines.append("按当前学习路线继续推进即可。")
            except Exception:  # noqa: BLE001
                lines.append("按当前学习路线继续推进即可。")
        else:
            lines.append("按当前学习路线继续推进即可。")
        return lines + [""]

    # ================= 导出 =================

    def export_daily_note(
        self, date: str, output_dir: str | Path | None = None
    ) -> dict:
        """导出某天笔记到 YYYY-MM-DD.md（幂等：覆盖式写入，内容稳定）。

        :param output_dir: 输出目录；缺省 docs/obsidian/；不存在自动创建。
        :raises OSError: 目录/文件不可写时抛出明确错误（由调用方决定是否崩溃）。
        :return: {"path": str, "written": bool, "chars": int}
        """
        out_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{date}.md"
        content = self.build_daily_note(date)
        path.write_text(content, encoding="utf-8")
        return {
            "path": str(path),
            "written": True,
            "chars": len(content),
            "date": date,
        }


def suggest_next_day(date: str) -> str:
    """辅助：给出推荐的下一天（仅日期工具）。"""
    return add_days(date, 1)
