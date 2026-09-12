"""Study Agent 程序入口。

只负责组装依赖并启动窗口，不承载任何业务逻辑。

命令行参数（仅开发/测试）：

--date YYYY-MM-DD  把“今天”模拟为指定日期（如 2026-09-05）。
                  只改内存中的 today provider，不写入数据库、
                  不改系统时间；不传则使用系统当前日期。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

# 允许直接 python app/main.py 或 python -m app.main 两种启动方式
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from app.ai.client import DeepSeekClient
from app.ai.long_term_context import load_long_term_context
from app.ai.planner import AIPlanner
from app.ai.summary import AISummaryGenerator
from app.database.connection import get_connection
from app.database.repository import TaskRepository
from app.database.study_plan_repository import (
    StudyPlanRepository,
    SummaryCacheRepository,
)
from app.services.daily_planner_service import DailyPlannerService
from app.services.date_service import DateService
from app.services.stats_service import StatsService
from app.services.study_plan_service import StudyPlanService
from app.services.summary_service import SummaryService
from app.services.task_review_service import TaskReviewService
from app.services.task_service import TaskService
from app.ui.main_window import MainWindow
from app.utils.date_utils import set_today_provider, today, to_date

# --date 必须严格匹配 YYYY-MM-DD（strptime 会放行 2026-9-5 这类非补零写法）
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_date_arg(argv: Optional[list[str]] = None) -> tuple[Optional[str], list[str]]:
    """解析命令行 --date 参数（仅开发/测试用）。

    :param argv: 参数列表（不含程序名）；缺省用 sys.argv[1:]。
    :return: (日期字符串 or None, 过滤掉 --date 后可用于 Qt 的剩余参数)。
    :raises SystemExit: --date 缺失值、格式非法、或日期不存在（如 2026-02-30）。
    """
    parser = argparse.ArgumentParser(
        prog="study-agent",
        description="Study Agent 桌面学习管理工具（--date 仅用于开发/测试）。",
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="把“今天”模拟为指定日期，仅开发/测试用；不传则用系统当前日期。",
    )
    ns, remaining = parser.parse_known_args(argv)
    if ns.date is None:
        return None, remaining
    value = ns.date
    if _DATE_RE.fullmatch(value) is None:
        parser.error(f"--date 必须是 YYYY-MM-DD 格式，收到：{value!r}")
    try:
        to_date(value)  # 进一步拒绝不存在的日期，如 2026-02-30
    except ValueError:
        parser.error(f"--date 不是真实存在的日期：{value!r}")
    return value, remaining


def _run_add_jd_cli(argv) -> int:
    """add-jd 子命令：入库一条 JD → 解析 → 更新技能频率/优先级 → 输出影响。

    用法：
      study-agent add-jd <file>
      study-agent add-jd --text "<JD文本>"
      可选：--company/--title/--dry-run/--no-ai/--db/--today
      --dry-run 不写入 JD/技能/计划数据。
    """
    from app.database.assessment_repository import AssessmentRepository
    from app.database.skill_repository import JdRepository, SkillRepository
    from app.services.jd_service import JdService, build_default_parse_ai
    from app.services.skill_service import SkillService

    parser = argparse.ArgumentParser(
        prog="study-agent add-jd",
        description="入库一条招聘 JD，并更新技能优先级（JD 只影响短期优先级）。",
    )
    parser.add_argument("file", nargs="?", default=None)
    parser.add_argument("--text", default=None)
    parser.add_argument("--company", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--db", default=None)
    parser.add_argument("--today", default=None)
    args, _ = parser.parse_known_args(argv)

    if args.text and args.file:
        parser.error("file 与 --text 只能提供其中一个")
    if not args.text and not args.file:
        parser.error("必须提供 JD 文件路径或 --text")

    if args.text:
        raw_text = args.text
    else:
        try:
            raw_text = Path(args.file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"读取 JD 文件失败: {e}", file=sys.stderr)
            return 1

    conn = get_connection(args.db)
    try:
        skill_repo = SkillRepository(conn)
        jd_repo = JdRepository(conn)
        assessment_repo = AssessmentRepository(conn)
        skill_service = SkillService(skill_repo, assessment_repo=assessment_repo)

        ai_client = DeepSeekClient()
        parse_ai = None
        if not args.no_ai and ai_client.is_configured():
            parse_ai = build_default_parse_ai(ai_client)
        jd_svc = JdService(
            jd_repo, skill_repo, skill_service,
            ai_client=ai_client, parse_ai=parse_ai,
        )

        today_str = args.today or today()
        use_ai = not args.no_ai
        if args.dry_run:
            # 干跑：不 seed、不写库，只输出解析 + 影响 + 每周预览
            impact = jd_svc.preview_impact(raw_text, use_ai=use_ai)
            _print_impact(impact, dry_run=True)
            _print_weekly(jd_svc.preview_weekly_priorities(today_str))
            return 0

        # 正式入库前：技能池为空时按 career_context 幂等 seed
        # （不覆盖已维护的状态/证据）
        if not skill_repo.list_all():
            skill_service.seed_from_career_context()
            skill_service.recompute_all_priority_scores()

        result = jd_svc.add_jd(
            raw_text, company=args.company, title=args.title,
            use_ai=use_ai,
        )
        status = "幂等跳过（同内容已存在）" if result["idempotent"] else "已入库"
        print(f"== add-jd: {status} (id={result['id']}) ==")
        _print_impact(result["impact"], dry_run=False)
        _print_weekly(jd_svc.preview_weekly_priorities(today_str))
        return 0
    finally:
        conn.close()


def _print_impact(impact: dict, dry_run: bool) -> None:
    tag = "[dry-run 预览]" if dry_run else "[已应用]"
    print(f"== 影响分析 {tag} ==")
    parsed = impact.get("parsed") or {}
    print(f"解析方法: {parsed.get('method')} | 方向: {parsed.get('direction') or '—'} | 实习: {'是' if parsed.get('intern') else '否'}")
    if not impact.get("affected_skills"):
        print("无匹配技能（不影响任何现有技能优先级）。")
        return
    print("受影响技能（before -> after）：")
    for e in impact["affected_skills"]:
        print(f"  - {e['explanation']}")
        print(f"    {e['skill']}: {e['freq_before']['must']}m/{e['freq_before']['plus']}p "
              f"-> {e['freq_after']['must']}m/{e['freq_after']['plus']}p | "
              f"priority {e['priority_before']:.4f} -> {e['priority_after']:.4f}")
    if impact.get("jd_gap_skills"):
        print("JD 缺口（需学习）：" + "、".join(e["skill"] for e in impact["jd_gap_skills"]))
    if impact.get("prerequisite_blocked"):
        print("前置未满足（不越级）：" + "、".join(e["skill"] for e in impact["prerequisite_blocked"]))
    if impact.get("not_rush"):
        print("不建议抢占主线（低优先级）：" + "、".join(impact["not_rush"]))


def _print_weekly(weekly: dict) -> None:
    print("== 未来 1~2 周纯规则预览（不建任务/不改计划） ==")
    for d in weekly["daily_focus"]:
        print(f"  {d['date']}: {d['note']}")
    if weekly.get("do_not_prioritize"):
        print("不优先项：" + "、".join(weekly["do_not_prioritize"]))


def _run_export_note_cli(argv) -> int:
    """export-note 子命令：导出某天的 Obsidian Markdown 学习笔记。

    用法：
      study-agent export-note [--date 2026-09-11] [--output 目录] [--db 路径]
      幂等：同一天重复导出为覆盖写、内容稳定；路径不存在自动创建。
    """
    from app.database.assessment_repository import AssessmentRepository
    from app.database.skill_repository import (
        JdRepository,
        LearningOutcomeRepository,
        SkillRepository,
    )
    from app.services.learning_outcome_service import LearningOutcomeService
    from app.services.notes_service import NotesService
    from app.services.skill_service import SkillService
    from app.services.study_plan_service import StudyPlanService

    parser = argparse.ArgumentParser(
        prog="study-agent export-note",
        description="导出某天的 Obsidian Markdown 学习笔记（幂等，只读知识库）。",
    )
    parser.add_argument("--date", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--db", default=None)
    args, _ = parser.parse_known_args(argv)

    date = args.date or today()
    if date and not re.fullmatch(_DATE_RE.pattern, date):
        parser.error(f"--date 必须是 YYYY-MM-DD，收到：{date!r}")

    conn = get_connection(args.db)
    try:
        repo = TaskRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        assessment_repo = AssessmentRepository(conn)
        # 需要学习计划阶段目标：确保默认计划存在（幂等，不破坏历史）
        sps = StudyPlanService(repo, plan_repo,
                               assessment_repo=assessment_repo)
        sps.ensure_default_plan()

        lo_repo = LearningOutcomeRepository(conn)
        outcome_service = LearningOutcomeService(lo_repo)

        ai_client = DeepSeekClient()
        outcome_service.ai_client = ai_client

        # 技能/JD（供“明日建议”纯规则预览；可选，出错不影响导出）
        jd_service = None
        try:
            skill_repo = SkillRepository(conn)
            skill_service = SkillService(
                skill_repo, plan_repo=plan_repo, assessment_repo=assessment_repo
            )
            if not skill_repo.list_all():
                skill_service.seed_from_career_context()
                skill_service.recompute_all_priority_scores()
            from app.services.jd_service import JdService

            jd_service = JdService(
                JdRepository(conn), skill_repo, skill_service,
                ai_client=ai_client,
            )
        except Exception:  # noqa: BLE001 - JD 预览失败不影响导出
            jd_service = None

        notes = NotesService(
            repo=repo,
            study_plan_service=sps,
            outcome_service=outcome_service,
            assessment_repo=assessment_repo,
            jd_service=jd_service,
        )
        try:
            res = notes.export_daily_note(date, output_dir=args.output)
        except OSError as e:
            print(f"导出失败：{e}", file=sys.stderr)
            return 2
        print(f"已导出：{res['path']}（{res['chars']} 字符）")
        return 0
    finally:
        conn.close()


def main() -> int:
    # 0) 子命令：不进 GUI
    if "add-jd" in sys.argv[1:]:
        return _run_add_jd_cli(sys.argv[2:])
    if "export-note" in sys.argv[1:]:
        return _run_export_note_cli(sys.argv[2:])
    # 1) 解析 --date（仅开发/测试）：注入“今天”。
    #    只在内存层面覆盖 date_utils.today()，不写数据库、不改系统时间；
    #    不传 --date 时保持默认（系统真实日期）。
    date_arg, qt_args = parse_date_arg()
    if date_arg is not None:
        set_today_provider(date_arg)

    # 2) Qt 只接收过滤后的参数（--date 及其取值已在 parse_date_arg 中剔除），
    #    避免 Qt 把开发参数当成未知选项报错。
    app = QApplication([sys.argv[0]] + qt_args)
    app.setApplicationName("Study Agent")
    # 托盘常驻：点 X 只是隐藏窗口，最后一个窗口消失也不退出，
    # 真正退出只由托盘菜单“退出”触发（QApplication.quit）
    app.setQuitOnLastWindowClosed(False)

    # 单实例：若已有一个实例在运行，通知其恢复前台后本进程退出
    from app.utils.single_instance import SingleInstanceGuard

    guard = SingleInstanceGuard()
    if not guard.acquire():
        return 0

    # 3) 组装依赖：SQLite -> Repository -> Service -> UI（UI 不直接碰 SQLite）
    conn = get_connection()
    repo = TaskRepository(conn)
    from app.database.skill_repository import LearningOutcomeRepository
    from app.services.learning_outcome_service import LearningOutcomeService

    outcome_service = LearningOutcomeService(LearningOutcomeRepository(conn))
    task_service = TaskService(repo, outcome_service=outcome_service)

    # 学习计划：确保默认研一计划已创建，供每日任务生成与阶段显示；
    # 注入 assessment_repo（Phase 8）让规则生成能读取掌握证据（薄弱优先/不重复）。
    plan_repo = StudyPlanRepository(conn)
    from app.database.assessment_repository import AssessmentRepository

    assessment_repo = AssessmentRepository(conn)
    # Phase A/C：技能 + JD（SkillService 确定性优先级；不注入则行为与旧版一致）
    from app.database.skill_repository import JdRepository, SkillRepository
    from app.services.skill_service import SkillService

    skill_repo = SkillRepository(conn)
    skill_service = SkillService(
        skill_repo, plan_repo=plan_repo, assessment_repo=assessment_repo
    )
    study_plan_service = StudyPlanService(
        repo, plan_repo,
        assessment_repo=assessment_repo,
        skill_service=skill_service,
    )
    study_plan_service.ensure_default_plan()

    # 存量“生成型新任务”description 回填（一次性、幂等；失败不阻止启动）
    try:
        repaired = study_plan_service.repair_existing_task_descriptions()
        if repaired:
            print(f"[startup] 已回填 {repaired} 条生成型任务的学习内容")
    except Exception:  # noqa: BLE001 - 回填失败不影响启动
        pass

    # 技能池为空时按 career_context 幂等 seed 并链接到现有主题（不覆盖已维护状态）
    if not skill_repo.list_all():
        skill_service.seed_from_career_context()
        skill_service.recompute_all_priority_scores()

    # AI 配置读取环境变量；未配置时 GUI 正常运行（本地功能不受影响）
    ai_client = DeepSeekClient()
    outcome_service.ai_client = ai_client  # 简历素材的 AI 组织（可选）
    from app.services.jd_service import JdService, build_default_parse_ai

    jd_service = JdService(
        JdRepository(conn), skill_repo, skill_service, ai_client=ai_client
    )
    # UI 里“分析 / 预览”也支持 AI 结构化解析（可选增强；失败回退规则）
    jd_service.parse_ai = (
        build_default_parse_ai(ai_client) if ai_client.is_configured() else None
    )
    # 长期学习上下文（职业目标/JD/技能路线/能力状态）：作为 AI 规划的长期依据；
    # 文件缺失/非法时返回 None，Planner 自动降级为旧行为，不影响启动。
    long_term_context = load_long_term_context()
    daily_planner = DailyPlannerService(
        repo,
        plan_repo,
        planner=AIPlanner(
            ai_client,
            long_term_context=long_term_context,
        ),
        study_plan_service=study_plan_service,
        assessment_repo=assessment_repo,
        skill_service=skill_service,
        jd_service=jd_service,
    )
    date_service = DateService(
        repo,
        study_plan_service=study_plan_service,
        daily_planner_service=daily_planner,
    )
    review_service = TaskReviewService(ai_client)

    # Phase 3D~6：验收 / 复习调度 / 额外学习 / 课外探索
    from app.services.assessment_service import AssessmentService
    from app.services.exploration_service import ExplorationService
    from app.services.extra_task_service import ExtraTaskService
    from app.services.review_service import ReviewService

    # 复习调度（依赖 TaskRepository + AssessmentRepository）
    review_scheduler = ReviewService(repo, assessment_repo)
    # 验收（判题成功后自动联动复习调度）
    assessment_service = AssessmentService(
        ai_client,
        assessment_repo=assessment_repo,
        review_service=review_scheduler,
        outcome_service=outcome_service,
    )
    extra_service = ExtraTaskService(
        repo,
        study_plan_service=study_plan_service,
        assessment_repo=assessment_repo,
    )
    exploration_service = ExplorationService()

    # 周/月总结（本地统计 + AI 解读 + 缓存）
    summary_service = SummaryService(
        stats_service=StatsService(repo),
        cache_repo=SummaryCacheRepository(conn),
        ai_generator=AISummaryGenerator(ai_client),
    )

    # 不显式传 today_provider：MainWindow 默认跟随 date_utils.today()，
    # 因此 --date 注入的日期会自动作用于整个应用（GUI 日期/阶段/任务/统计/AI）。
    # Phase D：Obsidian 每日笔记（供 UI 导出；未注入则 UI 隐藏导出能力）
    from app.services.notes_service import NotesService

    notes_service = NotesService(
        repo=repo,
        study_plan_service=study_plan_service,
        outcome_service=outcome_service,
        assessment_repo=assessment_repo,
        jd_service=jd_service,
    )

    window = MainWindow(
        task_service=task_service,
        date_service=date_service,
        review_service=review_service,
        study_plan_service=study_plan_service,
        daily_planner_service=daily_planner,
        summary_service=summary_service,
        assessment_service=assessment_service,
        assessment_repo=assessment_repo,
        review_scheduler=review_scheduler,
        extra_service=extra_service,
        exploration_service=exploration_service,
        skill_service=skill_service,
        jd_service=jd_service,
        outcome_service=outcome_service,
        notes_service=notes_service,
    )
    # 新实例启动请求 → 恢复/前置已有唯一实例（从托盘恢复或直接激活）
    if hasattr(window, "_restore_from_tray"):
        guard.restore_requested.connect(window._restore_from_tray)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
