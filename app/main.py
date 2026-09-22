"""Study Agent 程序入口。

只负责组装依赖并启动窗口，不承载任何业务逻辑。

命令行参数（仅开发/测试）：

--date YYYY-MM-DD  把“今天”模拟为指定日期（如 2026-09-05）。
                  只改内存中的 today provider，不写入数据库、
                  不改系统时间；不传则使用系统当前日期。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Optional

# 允许直接 python app/main.py 或 python -m app.main 两种启动方式
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from app.ai.client import AdaptiveAIClient, DeepSeekClient
from app.ai.config_service import AIConfigService
from app.ai.long_term_context import load_long_term_context
from app.ai.planner import AIPlanner
from app.ai.prompt_registry import PromptOverrideRepository, PromptRegistry
from app.ai.summary import AISummaryGenerator
from app.database.connection import (
    get_connection,
    get_raw_connection,
    get_readonly_connection,
    resolve_db_path,
)
from app.database.repository import TaskRepository
from app.database.study_plan_repository import (
    StudyPlanRepository,
    SummaryCacheRepository,
)
from app.services.daily_planner_service import DailyPlannerService
from app.services.date_service import DateService
from app.services.canonical_route_service import CanonicalRouteService
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

        ai_config_service = AIConfigService(db_path=str(resolve_db_path(args.db)))
        prompt_registry = PromptRegistry(PromptOverrideRepository(conn))
        ai_client = AdaptiveAIClient(ai_config_service.get_runtime_config)
        parse_ai = None
        if not args.no_ai and ai_client.is_configured():
            parse_ai = build_default_parse_ai(ai_client, prompt_registry)
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


def _print_summary_preview(preview: dict, dry_run: bool) -> None:
    tag = "[dry-run 预览]" if dry_run else "[已保存]"
    print(f"== 每日 JD 汇总 {tag} ==")
    print(f"日期: {preview.get('summary_date')} | 目标: {preview.get('target_type')} "
          f"| 样本岗位数: {preview.get('sample_count')}")
    if preview.get("errors"):
        print("数据错误（未写入）：")
        for e in preview["errors"]:
            print(f"  ! {e}")
        return
    sample = preview.get("sample_count") or 0
    print("已匹配技能：")
    for r in preview["matched"]:
        extra = (f" | must {r['must_count']} plus {r['plus_count']}"
                 if (r.get("must_count") or r.get("plus_count")) else "")
        print(f"  {r['name']}: {r['mention_count']}/{sample} "
              f"({r['frequency'] * 100:.1f}%){extra}")
    if preview.get("unmatched"):
        print("未匹配技能（已保留原始写法）：")
        for r in preview["unmatched"]:
            print(f"  {r['raw_skill_name']}: {r['mention_count']}/{sample} "
                  f"({r['frequency'] * 100:.1f}%)")


def _run_add_jd_summary_cli(argv):
    """add-jd-summary 子命令：录入/更新“某天 N 家岗位的技术汇总”。

    用法：
      study-agent add-jd-summary --date 2026-09-14 --sample-count 15
        --file summary.txt [--target internship] [--note ...] [--dry-run] [--db ...]
    """
    from app.database.jd_summary_repository import JdDailySummaryRepository
    from app.database.skill_repository import SkillRepository
    from app.services.jd_summary_service import (
        DEFAULT_TARGET_TYPE,
        JdSummaryService,
    )

    parser = argparse.ArgumentParser(
        prog="study-agent add-jd-summary",
        description="录入每日 JD 技术汇总（同一天同一目标幂等更新）。",
    )
    parser.add_argument("--date", required=True, help="汇总日期 YYYY-MM-DD")
    parser.add_argument("--sample-count", type=int, required=True,
                        help="当天人工查看的岗位总数（必须 > 0）")
    parser.add_argument("--target", default=DEFAULT_TARGET_TYPE)
    parser.add_argument("--file", default=None, help="汇总文本文件")
    parser.add_argument("--text", default=None, help="汇总文本")
    parser.add_argument("--note", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db", default=None)
    args, _ = parser.parse_known_args(argv)

    if args.text is not None and args.file is not None:
        parser.error("--file 与 --text 只能提供其中一个")
    if args.text is None and args.file is None:
        parser.error("必须提供 --file 或 --text")
    if args.text is not None:
        text = args.text
    else:
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"读取汇总文件失败: {e}", file=sys.stderr)
            return 1

    conn = get_connection(args.db)
    try:
        svc = JdSummaryService(
            JdDailySummaryRepository(conn), SkillRepository(conn)
        )
        if args.dry_run:
            preview = svc.preview_summary(
                text, args.sample_count, args.target, args.date
            )
            _print_summary_preview(preview, dry_run=True)
            return 0 if preview["valid"] else 1
        try:
            svc.save_summary(
                args.date, text, args.sample_count, args.target, note=args.note
            )
        except ValueError as e:
            print(f"保存失败：{e}", file=sys.stderr)
            return 1
        preview = svc.preview_summary(
            text, args.sample_count, args.target, args.date
        )
        _print_summary_preview(preview, dry_run=False)
        return 0
    finally:
        conn.close()


def _run_jd_trends_cli(argv):
    """jd-trends 子命令：输出近期市场频率（只基于每日汇总）。"""
    from app.database.jd_summary_repository import JdDailySummaryRepository
    from app.database.skill_repository import SkillRepository
    from app.services.jd_summary_service import (
        DEFAULT_TARGET_TYPE,
        JdSummaryService,
    )

    parser = argparse.ArgumentParser(
        prog="study-agent jd-trends",
        description="近期 JD 技术趋势（默认只看每日汇总，不并入单条 JD）。",
    )
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--target", default=DEFAULT_TARGET_TYPE)
    parser.add_argument("--end", default=None, help="窗口结束日，默认今天")
    parser.add_argument("--db", default=None)
    args, _ = parser.parse_known_args(argv)

    conn = get_connection(args.db)
    try:
        svc = JdSummaryService(
            JdDailySummaryRepository(conn), SkillRepository(conn)
        )
        end = args.end or today()
        trend = svc.compute_skill_trends(end, args.days, args.target)
        print(f"== 近 {trend['window_days']} 天 JD 技术趋势 "
              f"[{trend['start_date']} ~ {trend['end_date']}] "
              f"| 目标 {trend['target_type']} | 样本 {trend['sample_count']} 个岗位 ==")
        if not trend["skills"]:
            print("窗口内暂无每日汇总数据。")
        for r in trend["skills"]:
            print(f"  {r['name']}: {r['mention_count']}"
                  f" ({r['frequency'] * 100:.1f}%)")
        if trend.get("unmatched"):
            print("未匹配技能：")
            for r in trend["unmatched"]:
                print(f"  {r['name']}: {r['mention_count']}"
                      f" ({r['frequency'] * 100:.1f}%)")
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

        ai_config_service = AIConfigService(db_path=str(resolve_db_path(args.db)))
        prompt_registry = PromptRegistry(PromptOverrideRepository(conn))
        ai_client = AdaptiveAIClient(ai_config_service.get_runtime_config)
        outcome_service.ai_client = ai_client
        outcome_service.prompt_registry = prompt_registry

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


def _run_past_task_preflight(repo, task_service, today_str: str) -> bool:
    """启动前置：历史未确认正式学习任务补确认。

    必须在 DateService.process_date_transition（自动归档 active -> not_done、
    生成今日计划）之前执行。

    :return: True 继续启动；False 终止本次启动（不进入今日流程、不改数据）。
    """
    from PySide6.QtWidgets import QDialog

    from app.services.past_task_service import PastTaskConfirmationService

    service = PastTaskConfirmationService(repo, task_service)
    try:
        unresolved = service.find_unresolved(today_str)
    except Exception:  # noqa: BLE001 - 查询异常不应卡死启动（保持旧行为）
        return True
    if not unresolved:
        return True

    from app.ui.dialogs import show_warning
    from app.ui.past_task_dialog import PastTaskConfirmationDialog

    route_names = {}
    try:
        from app.database.learning_route_repository import (
            LearningRouteRepository,
        )

        route_names = {
            r.id: r.name for r in LearningRouteRepository(repo.conn).list_all()
        }
    except Exception:  # noqa: BLE001 - 路线表缺失时不显示路线标签
        route_names = {}

    dlg = PastTaskConfirmationDialog(unresolved, route_names=route_names)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False  # 取消 / X / Esc：不默认判未完成、不进入今天
    try:
        service.apply_decisions(dlg.result_decisions())
    except Exception as e:  # noqa: BLE001 - 更新失败则不继续启动
        show_warning(None, f"任务状态更新失败：{e}")
        return False
    return True


def _run_six_routes_cli(argv) -> int:
    """six-routes 子命令：canonical 六路线 seed + 历史迁移 inventory/preview/apply。

    用法：
      study-agent six-routes inventory [--db PATH] [--json]
      study-agent six-routes preview   [--db PATH] [--json]
      study-agent six-routes apply     [--db PATH] [--strict] [--json]
      study-agent six-routes seed      [--db PATH]

    安全性（v1 stabilization）：
    - inventory：真正只读连接（mode=ro + query_only）；旧 schema 缺列时
      在临时只读副本上盘点，绝不修改正式库；
    - preview：在临时可写副本上模拟 migrate + seed，正式库零修改；
    - apply：先在不接触正式库的副本上 pre-flight；conflict 时直接中止，
      不会对正式库做任何业务修改。
    """
    import json as _json
    import sqlite3

    from app.database.learning_route_repository import LearningRouteRepository
    from app.database.schema import migrate_stepwise
    from app.database.skill_repository import SkillRepository
    from app.database.study_plan_repository import StudyPlanRepository
    from app.services.canonical_route_service import CanonicalRouteService
    from app.services.route_migration_service import RouteMigrationService

    parser = argparse.ArgumentParser(
        prog="study-agent six-routes",
        description="canonical 六技术路线 seed 与历史 Topic 安全迁移。",
    )
    parser.add_argument("action", choices=["inventory", "preview", "apply", "seed"])
    parser.add_argument("--db", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--strict", action="store_true",
        help="apply 时若存在 conflict 则整体拒绝（默认仅跳过冲突项）",
    )
    args, _ = parser.parse_known_args(argv)

    from app.diagnostics import release_migration as rm

    def _services(conn):
        route_repo = LearningRouteRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        skill_repo = SkillRepository(conn)
        svc = CanonicalRouteService(conn, route_repo, plan_repo, skill_repo)
        mig = RouteMigrationService(conn, route_repo, plan_repo)
        return route_repo, plan_repo, skill_repo, svc, mig

    def _print_inventory(inv):
        if args.json:
            print(_json.dumps(inv, ensure_ascii=False, indent=2))
            return
        print(f"schema_version: {inv['schema_version']}")
        print(f"legacy_route_id: {inv['legacy_route_id']}")
        for r in inv["routes"]:
            print(f"  route[{r['id']}] key={r.get('route_key')} "
                  f"{r['name']} ({r['status']}, p={r['priority']}, "
                  f"planning={r['planning_enabled']})")
        print(f"counts: plans={len(inv['plans'])} "
              f"topics={inv['study_topics']} tasks={inv['tasks']} "
              f"kp={inv['knowledge_points']} "
              f"assessments={inv['assessment_attempts']} "
              f"reviews={inv['review_schedule']} "
              f"planner_decisions={inv['planner_decisions']} "
              f"skills={inv['skills']} "
              f"outcomes={inv['learning_outcomes']}")
        if inv.get("duplicate_active_plans"):
            print("  !! duplicate active plans:", inv["duplicate_active_plans"])

    def _print_preview(preview):
        if args.json:
            payload = {
                "summary": preview.summary(),
                "entries": [
                    {
                        "old_topic_id": e.old_topic_id,
                        "old_name": e.old_name,
                        "action": e.action,
                        "target_route": e.target_route_name,
                        "target_phase": e.target_phase,
                        "tasks": e.task_count,
                        "kp": e.kp_count,
                        "assessments": e.assessment_count,
                        "reviews": e.review_count,
                        "conflicts": e.conflicts,
                    }
                    for e in preview.entries
                ],
            }
            print(_json.dumps(payload, ensure_ascii=False, indent=2))
            return
        s = preview.summary()
        print(f"preview: MOVE={s['move']} "
              f"(migratable={s['move_migratable']}) "
              f"SPLIT_NEW={s['split_new']} "
              f"KEEP_LEGACY={s['keep_legacy']} "
              f"MANUAL_REVIEW={s['manual_review']} "
              f"conflicts={s['conflicts']}")
        for e in preview.entries:
            tag = e.action
            extra = (f" conflicts={e.conflicts}" if e.conflicts else "")
            target = f" -> {e.target_route_name}/{e.target_phase}" \
                if e.target_route_name else ""
            print(f"  [{tag}] topic#{e.old_topic_id} {e.old_name}"
                  f"{target} (tasks={e.task_count}, kp={e.kp_count}, "
                  f"assess={e.assessment_count}, "
                  f"reviews={e.review_count}){extra}")

    # ---------- inventory：真正只读 ----------
    if args.action == "inventory":
        try:
            conn = get_readonly_connection(args.db)
            try:
                inv = _services(conn)[4].inventory()
            finally:
                conn.close()
        except sqlite3.Error:
            # 旧 schema（如 v14 无 route_key）→ 在临时只读副本上尝试
            try:
                with rm.readonly_copy(args.db) as c:
                    inv = _services(c)[4].inventory()
            except sqlite3.Error as e:
                print(
                    "six-routes inventory 需要 v15+ schema"
                    f"（当前数据库尚未迁移：{e}）。\n"
                    "请先运行：python -m app.main db-release inventory --db "
                    f'"{args.db}"'  # 只读盘点兼容旧 schema
                )
                return 2
        _print_inventory(inv)
        return 0

    # ---------- preview：临时副本上模拟，正式库零修改 ----------
    if args.action == "preview":
        with rm.working_copy(args.db, migrate=True) as c:
            _, _, _, svc, mig = _services(c)
            svc.ensure_pre_migration()
            preview = mig.preview()
            _print_preview(preview)
        return 1 if preview.has_conflicts else 0

    # ---------- apply：先 pre-flight，再改正式库 ----------
    if args.action == "apply":
        pre = rm.dry_run_on_copy(args.db)
        if not pre.get("ok"):
            print(
                "apply aborted (preflight failed): "
                f"stage={pre.get('stage')} conflicts={pre.get('conflicts')} "
                f"source_problems={pre.get('source_problems')} "
                f"error={pre.get('error')}"
            )
            return 1
        conn = get_raw_connection(args.db)
        try:
            migrate_stepwise(conn)
            _, _, _, svc, mig = _services(conn)
            result = mig.apply(allow_partial=not args.strict)
            if args.json:
                print(_json.dumps({
                    "applied": result.get("applied"),
                    "reason": result.get("reason"),
                    "moved": result.get("moved"),
                    "skipped": result.get("skipped"),
                    "summary": result.get("summary"),
                }, ensure_ascii=False, indent=2))
            else:
                print(f"apply: applied={result.get('applied')} "
                      f"reason={result.get('reason')} "
                      f"summary={result.get('summary')}")
                for m in result.get("moved", []):
                    print(f"  moved topic#{m.get('old_topic_id')} "
                          f"{m.get('old_name')} -> "
                          f"{m.get('target_route_key')}/{m.get('target_phase')}")
                for sk in result.get("skipped", []):
                    print(f"  skipped topic#{sk.get('old_topic_id')} "
                          f"conflicts={sk.get('conflicts')}")
            svc.ensure_topics(svc.ensure_pre_migration()["routes"])
            svc.ensure_extra_skills()
            svc.ensure_route_skills(svc.ensure_pre_migration()["routes"])
            svc.ensure_topic_skill_links()
            if result.get("applied") is False and \
                    result.get("reason") == "conflicts_present":
                return 1
            return 0
        finally:
            conn.close()

    # ---------- seed：显式完整 ensure_all ----------
    conn = get_raw_connection(args.db)
    try:
        migrate_stepwise(conn)
        _, _, _, svc, _ = _services(conn)
        res = svc.ensure_all()
        print(f"seed: routes={res['route_ids']} "
              f"route_skills=+{res['route_skill_links']} "
              f"topic_links=+{res['topic_skill_links']} "
              f"migration={res['migration']}")
        return 0
    finally:
        conn.close()


def _run_capability_cli(argv) -> int:
    """capability-backfill 子命令：从已有真实证据提取 capability（preview/apply）。

    用法：
      study-agent capability-backfill preview [--db PATH] [--json]
      study-agent capability-backfill apply   [--db PATH] [--json]
    绝不从 mastery 反推 capability；PROJECT(5) 不会生成。
    """
    import json as _json

    from app.database.capability_repository import CapabilityEvidenceRepository
    from app.services.capability_service import CapabilityService

    parser = argparse.ArgumentParser(
        prog="study-agent capability-backfill",
        description="从 Task / Assessment / experiment Outcome 提取 capability。",
    )
    parser.add_argument("action", choices=["preview", "apply"])
    parser.add_argument("--db", default=None)
    parser.add_argument("--json", action="store_true")
    args, _ = parser.parse_known_args(argv)

    conn = get_connection(args.db)
    try:
        svc = CapabilityService(conn, CapabilityEvidenceRepository(conn))
        if args.action == "preview":
            data = svc.preview()
        else:
            data = svc.backfill()
        if args.json:
            print(_json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"capability {args.action}:")
            for k, v in data.items():
                if k == "entries":
                    continue
                print(f"  {k}: {v}")
        return 0
    finally:
        conn.close()


def _run_planner_diagnostic_cli(argv) -> int:
    """planner-diagnostic 子命令（只读）：

      study-agent planner-diagnostic [--db PATH] [--date YYYY-MM-DD]
                                     [--route ROUTE_KEY] [--json]

    输出每条 active 学习路线的确定性 planner 状态（phase / legal topics /
    next activity / Tier / reasons / candidate pool）。
    **不创建 Task、不写 planner_decisions、不调用 AI、不输出 secret。**
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="study-agent planner-diagnostic",
        description="只读打印 Planner 的确定性候选与优先级状态。",
    )
    parser.add_argument("--db", default=None)
    parser.add_argument("--date", default=None)
    parser.add_argument("--route", default=None, help="route_key，例如 R3_LLM_INFRA")
    parser.add_argument("--json", action="store_true")
    args, _ = parser.parse_known_args(argv)

    from app.diagnostics.planner_diagnostic import (
        format_planner_diagnostic,
        run_planner_diagnostic,
    )

    conn = get_raw_connection(args.db, read_only=True)
    try:
        data = run_planner_diagnostic(
            conn, plan_date=args.date, route_key=args.route
        )
        if args.json:
            import json as _json

            print(_json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(format_planner_diagnostic(data))
        return 0
    finally:
        conn.close()


def _run_db_release_cli(argv) -> int:
    """db-release 子命令：正式 DB 备份 / 盘点 / 逐级迁移 / 完整性校验。

    用法：
      study-agent db-release backup     --db PATH [--out-dir DIR]
      study-agent db-release inventory  --db PATH [--json] [--save SNAP.json]
      study-agent db-release migrate    --db PATH [--apply-capability]
                                        [--save-snapshot SNAP.json]
      study-agent db-release verify     --db PATH [--before SNAP.json] [--json]
    """
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(
        prog="study-agent db-release",
        description="Learning System v1 正式库迁移/校验工具（不调 AI）。",
    )
    parser.add_argument(
        "action", choices=["backup", "inventory", "migrate", "verify"]
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--before", default=None)
    parser.add_argument("--save", default=None)
    parser.add_argument("--save-snapshot", default=None)
    parser.add_argument("--apply-capability", action="store_true")
    parser.add_argument("--json", action="store_true")
    args, _ = parser.parse_known_args(argv)

    from app.diagnostics import release_migration as rm

    if args.action == "backup":
        path = rm.backup_database(args.db, out_dir=args.out_dir)
        print(f"backup: {path}")
        return 0

    # inventory/verify 严格只读；migrate 使用不自动迁移的连接
    if args.action in ("inventory", "verify"):
        conn = get_raw_connection(args.db, read_only=True)
    else:
        conn = get_raw_connection(args.db)
    try:
        if args.action == "inventory":
            data = rm.inventory(conn)
            if args.save:
                Path(args.save).write_text(rm.dumps(data), encoding="utf-8")
                print(f"snapshot saved: {args.save}")
            print(_json.dumps(data, ensure_ascii=False, indent=2)
                  if args.json else _inventory_text(data))
            return 0

        if args.action == "migrate":
            before = None
            if args.before and Path(args.before).exists():
                before = _json.loads(Path(args.before).read_text(encoding="utf-8"))
            else:
                # 迁移前先用只读连接盘点（不触发任何迁移）
                ro = get_readonly_connection(args.db)
                try:
                    before = rm.inventory(ro)
                finally:
                    ro.close()
            # pre-flight：在临时副本上验证，正式库尚未发生任何修改
            pre = rm.dry_run_on_copy(args.db)
            if not pre.get("ok"):
                print(_json.dumps({
                    "aborted": True,
                    "stage": pre.get("stage"),
                    "conflicts": pre.get("conflicts"),
                    "source_problems": pre.get("source_problems"),
                    "error": pre.get("error"),
                    "before": before,
                }, ensure_ascii=False, indent=2) if args.json else
                    f"migrate aborted (preflight): stage={pre.get('stage')} "
                    f"conflicts={pre.get('conflicts')} "
                    f"source_problems={pre.get('source_problems')} "
                    f"error={pre.get('error')}")
                return 1
            stats = _run_release_migrate(
                conn, apply_capability=args.apply_capability,
                skip_preflight=True,
            )
            after = rm.inventory(conn)
            report = {
                "migration": stats,
                "before": before,
                "after": after,
                "verify": rm.verify(conn, before=before),
            }
            if args.save_snapshot:
                Path(args.save_snapshot).write_text(
                    rm.dumps(after), encoding="utf-8"
                )
                print(f"snapshot saved: {args.save_snapshot}")
            print(_json.dumps(report, ensure_ascii=False, indent=2)
                  if args.json else _migrate_text(report))
            return 0 if report["verify"]["ok"] else 1

        # verify
        before = None
        if args.before and Path(args.before).exists():
            before = _json.loads(Path(args.before).read_text(encoding="utf-8"))
        result = rm.verify(conn, before=before)
        print(_json.dumps(result, ensure_ascii=False, indent=2)
              if args.json else _verify_text(result))
        return 0 if result["ok"] else 1
    finally:
        conn.close()


MIGRATION_GATE_ALLOW_FLAG = "--allow-auto-migrate"


def migration_gate_status(db_path) -> dict:
    """GUI 启动前的旧库迁移闸门（只读探测）。

    - 不存在的 DB / 新空库：允许（正常创建）；
    - user_version >= SCHEMA_VERSION：允许；
    - 旧版本（已存在且有业务表）：**禁止**自动升级，返回指引。
    """
    import sqlite3

    from app.database.schema import SCHEMA_VERSION

    path = Path(db_path)
    if not path.exists():
        return {"blocked": False, "version": None, "reason": "new_db"}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            has_business = bool(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='tasks' LIMIT 1"
            ).fetchone())
        finally:
            conn.close()
    except sqlite3.Error:
        # fail closed：已存在的 DB 无法安全读取 → 禁止自动迁移
        return {
            "blocked": True,
            "version": None,
            "target": SCHEMA_VERSION,
            "db_path": str(path),
            "reason": "unreadable_db",
        }
    if version >= SCHEMA_VERSION:
        return {"blocked": False, "version": version, "reason": "current"}
    if version == 0 and not has_business:
        return {"blocked": False, "version": version, "reason": "empty_db"}
    return {
        "blocked": True,
        "version": version,
        "target": SCHEMA_VERSION,
        "db_path": str(path),
        "reason": "old_schema",
    }


def migration_gate_message(status: dict) -> str:
    db = status.get("db_path", "data/study_agent.db")
    if status.get("reason") == "unreadable_db":
        return (
            "=" * 68 + "\n"
            "数据库无法安全读取（read-only 探测失败）。\n"
            "为避免在未知状态下自动迁移，GUI 启动已停止（fail closed）。\n\n"
            "请先：\n"
            "  1) 确认文件不是正在被其它进程占用（先关闭旧实例）；\n"
            "  2) 从备份恢复，或人工检查数据库文件完整性；\n"
            "  3) 用命令行复查：\n"
            f'     python -m app.main db-release inventory --db "{db}"\n'
            + "=" * 68
        )
    v = status.get("version")
    target = status.get("target")
    return (
        "=" * 68 + "\n"
        f"检测到旧版本数据库（schema v{v} < v{target}）。\n"
        "为避免绕过备份/校验流程自动升级，GUI 启动已停止。\n\n"
        "请先在命令行执行（按顺序）：\n"
        f'  python -m app.main db-release backup    --db "{db}"\n'
        f'  python -m app.main db-release inventory --db "{db}" --save before.json\n'
        f'  python -m app.main db-release migrate   --db "{db}" --before before.json --apply-capability\n'
        f'  python -m app.main db-release verify    --db "{db}" --before before.json\n\n'
        "全部通过后再启动 GUI。\n"
        f"（开发/测试如需直接自动迁移，加 {MIGRATION_GATE_ALLOW_FLAG}）\n"
        + "=" * 68
    )


def _conn_db_path(conn) -> str | None:
    """从连接取主 DB 文件路径（用于 pre-flight 副本）。"""
    try:
        for _seq, name, path in conn.execute("PRAGMA database_list"):
            if name == "main" and path:
                return str(path)
    except Exception:  # noqa: BLE001
        return None
    return None


def _run_release_migrate(
    conn, apply_capability: bool = False, skip_preflight: bool = False
) -> dict:
    """schema 逐级迁移 → canonical seed/迁移（→ 可选 capability backfill）。

    默认先在不接触正式库的临时副本上 pre-flight；conflict/错误时抛异常，
    保证正式库在任何业务修改前就中止。
    """
    if not skip_preflight:
        db_path = _conn_db_path(conn)
        if db_path:
            from app.diagnostics import release_migration as _rm

            pre = _rm.dry_run_on_copy(db_path)
            if not pre.get("ok"):
                raise RuntimeError(
                    "migration preflight failed: "
                    f"stage={pre.get('stage')} "
                    f"conflicts={pre.get('conflicts')} "
                    f"source_problems={pre.get('source_problems')} "
                    f"error={pre.get('error')}"
                )
    from app.database.assessment_repository import AssessmentRepository
    from app.database.capability_repository import CapabilityEvidenceRepository
    from app.database.learning_route_repository import LearningRouteRepository
    from app.database.repository import TaskRepository
    from app.database.schema import get_schema_version, migrate_stepwise
    from app.database.skill_repository import SkillRepository
    from app.database.study_plan_repository import StudyPlanRepository
    from app.database.topic_learning_repository import (
        TopicLearningComponentRepository,
    )
    from app.services.canonical_route_service import CanonicalRouteService
    from app.services.capability_service import CapabilityService
    from app.services.topic_learning_profile_service import (
        TopicLearningProfileService,
    )

    steps: list[int] = []
    version_before = get_schema_version(conn)
    final = migrate_stepwise(conn, on_step=steps.append)
    plan_repo = StudyPlanRepository(conn)
    route_repo = LearningRouteRepository(conn)
    skill_repo = SkillRepository(conn)
    tl = TopicLearningProfileService(
        conn, TopicLearningComponentRepository(conn)
    )
    canonical = CanonicalRouteService(
        conn, route_repo, plan_repo, skill_repo,
        topic_learning_service=tl,
    ).ensure_all()
    mig = canonical.get("migration") or {}
    conflicts = int(mig.get("conflicts") or 0)
    if conflicts:
        raise RuntimeError(
            f"六路线迁移存在 conflicts={conflicts}，已停止（请先人工处理）"
        )
    cap_stats = None
    if apply_capability:
        svc = CapabilityService(conn, CapabilityEvidenceRepository(conn))
        preview = svc.preview()
        if preview.get("conflicts"):
            raise RuntimeError(
                f"capability backfill 存在 conflicts={preview['conflicts']}，已停止"
            )
        cap_stats = svc.backfill()
    return {
        "schema_version_before": version_before,
        "schema_version_after": final,
        "steps": steps,
        "routes": canonical.get("route_ids"),
        "migration": mig,
        "route_skill_links": canonical.get("route_skill_links"),
        "topic_skill_links": canonical.get("topic_skill_links"),
        "capability_backfill": cap_stats,
    }


def _nonzero(d):
    """文本输出时只展示真正有内容的表，避免全零噪音。"""
    return {k: v for k, v in (d or {}).items() if v}


def _inventory_text(data: dict) -> str:
    lines = [f"schema_version: {data.get('schema_version')}", "counts:"]
    for k, v in (data.get("counts") or {}).items():
        lines.append(f"  {k}: {v}")
    for k in ("tasks_done", "mastery_nonzero", "tasks_null_route",
              "kp_null_route", "legacy_route", "legacy_topic_count",
              "duplicate_active_plans", "same_name_kp_conflicts"):
        if data.get(k) is not None:
            lines.append(f"{k}: {data.get(k)}")
    lines.append(f"canonical_route_keys: {data.get('canonical_route_keys')}")
    return "\n".join(lines)


def _migrate_text(report: dict) -> str:
    m = report["migration"]
    lines = [
        f"schema: v{m['schema_version_before']} → v{m['schema_version_after']}",
        f"steps: {m['steps']}",
        f"routes: {m['routes']}",
        f"route_skills: +{m.get('route_skill_links')}",
        f"topic_skill_links: +{m.get('topic_skill_links')}",
        f"migration: {m.get('migration')}",
        f"capability_backfill: {m.get('capability_backfill')}",
    ]
    v = report["verify"]
    lines.append(f"verify.ok: {v['ok']}")
    lines.append(f"integrity_check: {v.get('integrity_check')}")
    lines.append(f"foreign_key_problems: {len(v.get('foreign_key_problems') or [])}")
    if v["route_problems"]:
        lines.append(f"route_problems: {v['route_problems']}")
    if v["evidence_problems"]:
        lines.append(f"evidence_problems: {v['evidence_problems']}")
    if v.get("component_problems"):
        lines.append(f"component_problems: {v['component_problems']}")
    if v["history_decreases"]:
        lines.append(f"history_decreases: {v['history_decreases']}")
    if v.get("history_fingerprint_changes"):
        lines.append(
            f"history_fingerprint_changes: {v['history_fingerprint_changes']}"
        )
    if v.get("history_id_changes"):
        lines.append(f"history_id_changes: {v['history_id_changes']}")
    if v.get("history_preserved"):
        lines.append(
            f"history_preserved: {_nonzero(v['history_preserved'])}"
        )
    if v.get("history_new_rows"):
        lines.append(
            f"history_new_rows: {_nonzero(v['history_new_rows'])}"
        )
    if v.get("history_missing_ids"):
        lines.append(f"history_missing_ids: {v['history_missing_ids']}")
    if v.get("history_modified_rows"):
        lines.append(
            f"history_modified_rows: {v['history_modified_rows']}"
        )
    if v.get("historical_row_field_check"):
        lines.append(
            f"historical_row_field_check: "
            f"{v['historical_row_field_check']}"
        )
    return "\n".join(lines)


def _verify_text(result: dict) -> str:
    lines = [f"schema_version: {result['schema_version']}",
             f"verify.ok: {result['ok']}"]
    lines.append(f"integrity_check: {result.get('integrity_check')}")
    lines.append(
        f"foreign_key_problems: {result.get('foreign_key_problems')}"
    )
    lines.append(f"route_problems: {result['route_problems']}")
    lines.append(f"evidence_problems: {result['evidence_problems']}")
    lines.append(f"component_problems: {result.get('component_problems')}")
    if result.get("before"):
        lines.append(f"history_decreases: {result['history_decreases']}")
        lines.append(
            f"history_fingerprint_changes: "
            f"{result.get('history_fingerprint_changes')}"
        )
        lines.append(
            f"history_id_changes: {result.get('history_id_changes')}"
        )
        lines.append(
            f"history_preserved: "
            f"{_nonzero(result.get('history_preserved'))}"
        )
        lines.append(
            f"history_new_rows: "
            f"{_nonzero(result.get('history_new_rows'))}"
        )
        lines.append(
            f"history_missing_ids: {result.get('history_missing_ids')}"
        )
        lines.append(
            f"history_modified_rows: {result.get('history_modified_rows')}"
        )
        if result.get("historical_row_field_check"):
            lines.append(
                f"historical_row_field_check: "
                f"{result.get('historical_row_field_check')}"
            )
    return "\n".join(lines)


def main() -> int:
    # 0) 子命令：不进 GUI
    if "add-jd-summary" in sys.argv[1:]:
        return _run_add_jd_summary_cli(sys.argv[2:])
    if "jd-trends" in sys.argv[1:]:
        return _run_jd_trends_cli(sys.argv[2:])
    if "add-jd" in sys.argv[1:]:
        return _run_add_jd_cli(sys.argv[2:])
    if "export-note" in sys.argv[1:]:
        return _run_export_note_cli(sys.argv[2:])
    if "six-routes" in sys.argv[1:]:
        return _run_six_routes_cli(sys.argv[2:])
    if "capability-backfill" in sys.argv[1:]:
        return _run_capability_cli(sys.argv[2:])
    if "planner-diagnostic" in sys.argv[1:]:
        return _run_planner_diagnostic_cli(sys.argv[2:])
    if "db-release" in sys.argv[1:]:
        return _run_db_release_cli(sys.argv[2:])
    # 1) 解析 --date（仅开发/测试）：注入“今天”。
    #    只在内存层面覆盖 date_utils.today()，不写数据库、不改系统时间；
    #    不传 --date 时保持默认（系统真实日期）。
    date_arg, qt_args = parse_date_arg()
    if date_arg is not None:
        set_today_provider(date_arg)

    # 2) Migration Gate：旧版本正式 DB 禁止绕过备份/校验流程自动升级
    allow_auto_migrate = (
        MIGRATION_GATE_ALLOW_FLAG in sys.argv[1:]
        or os.environ.get("STUDY_AGENT_ALLOW_AUTO_MIGRATE") == "1"
    )
    qt_args = [a for a in qt_args if a != MIGRATION_GATE_ALLOW_FLAG]
    gate = migration_gate_status(resolve_db_path())
    if gate.get("blocked") and not allow_auto_migrate:
        print(migration_gate_message(gate))
        print("migration gate: blocked (run db-release backup/migrate/verify first)")
        return 3

    # 3) Qt 只接收过滤后的参数（--date 及其取值已在 parse_date_arg 中剔除），
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
    # Phase 3：Capability Evidence（从已有真实证据确定性提取，不调 LLM）
    from app.database.capability_repository import CapabilityEvidenceRepository
    from app.services.capability_service import CapabilityService

    capability_service = CapabilityService(
        conn, CapabilityEvidenceRepository(conn)
    )
    outcome_service.capability_service = capability_service
    task_service = TaskService(
        repo, outcome_service=outcome_service,
        capability_service=capability_service,
    )

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
    # Phase B：学习路线数据层（当前仅提供数据能力，不接 Planner / UI）
    from app.database.learning_route_repository import LearningRouteRepository
    from app.services.learning_route_service import LearningRouteService
    from app.services.route_plan_service import RoutePlanService

    route_repo = LearningRouteRepository(conn)
    route_service = LearningRouteService(route_repo, skill_repo=skill_repo)
    # Phase 2：Topic Learning Activity 统一入口
    from app.database.topic_learning_repository import (
        TopicLearningComponentRepository,
    )
    from app.services.topic_learning_profile_service import (
        TopicLearningProfileService,
    )

    topic_learning_service = TopicLearningProfileService(
        conn, TopicLearningComponentRepository(conn)
    )
    skill_service.topic_learning_service = topic_learning_service
    route_plan_service = RoutePlanService(
        plan_repo, repo, assessment_repo,
        topic_learning_service=topic_learning_service,
    )
    # Phase F：route-specific curriculum gap 需要 route_skills
    skill_service.route_repo = route_repo
    # Phase E：路线进度 / 掌握 / 复习状态统一计算
    from app.services.route_progress_service import RouteProgressService
    from app.services.route_repair_service import repair_route_assignments

    route_progress_service = RouteProgressService(
        repo, assessment_repo, plan_repo, route_repo,
        topic_learning_service=topic_learning_service,
        capability_service=capability_service,
    )
    # Phase 4：Practice / Project Layer（独立于 LearningRoute）
    from app.database.practice_repository import (
        PracticeMilestoneRepository,
        PracticeOutputRepository,
        PracticeProjectRepository,
        PracticeTopicEvidenceRepository,
    )
    from app.services.practice_capability_service import (
        PracticeCapabilityService,
    )
    from app.services.practice_project_service import PracticeProjectService

    practice_evidence_repo = PracticeTopicEvidenceRepository(conn)
    practice_service = PracticeProjectService(
        conn,
        project_repo=PracticeProjectRepository(conn),
        milestone_repo=PracticeMilestoneRepository(conn),
        output_repo=PracticeOutputRepository(conn),
        route_repo=route_repo,
        plan_repo=plan_repo,
        skill_repo=skill_repo,
        evidence_repo=practice_evidence_repo,
    )
    # Phase 5：Practice → Capability（唯一能产生 Level 5 的路径；不自动 backfill）
    practice_capability_service = PracticeCapabilityService(
        conn,
        evidence_repo=practice_evidence_repo,
        service=capability_service,
        project_repo=PracticeProjectRepository(conn),
        output_repo=PracticeOutputRepository(conn),
        plan_repo=plan_repo,
        route_repo=route_repo,
    )
    # Phase 6：Practice Planner Feedback（Project Readiness + 确定性 Tier 排序）
    from app.database.practice_repository import (
        PracticeRequirementRepository,
    )
    from app.services.planner_feedback import PlannerFeedbackService
    from app.services.practice_readiness import PracticeReadinessService

    practice_requirement_repo = PracticeRequirementRepository(conn)
    practice_readiness_service = PracticeReadinessService(
        conn,
        requirement_repo=practice_requirement_repo,
        project_repo=PracticeProjectRepository(conn),
        plan_repo=plan_repo,
        route_repo=route_repo,
        capability_repo=capability_service.repo,
        topic_learning_service=topic_learning_service,
        task_repo=repo,
    )
    planner_feedback_service = PlannerFeedbackService(
        conn,
        readiness_service=practice_readiness_service,
        plan_repo=plan_repo,
        route_repo=route_repo,
        skill_service=skill_service,
        task_repo=repo,
    )
    # 幂等修复历史 route 归属（绝不猜 manual NULL / ordinary todo）
    try:
        repaired = repair_route_assignments(conn, assessment_repo)
        if any(repaired.values()):
            print(f"[startup] route 归属修复：{repaired}")
    except Exception:  # noqa: BLE001 - 修复失败不影响启动
        pass
    study_plan_service = StudyPlanService(
        repo, plan_repo,
        assessment_repo=assessment_repo,
        skill_service=skill_service,
        learning_route_repo=route_repo,
        topic_learning_service=topic_learning_service,
    )
    study_plan_service.ensure_default_plan()

    # Step 6：近期市场需求（Daily Summary 优先，individual JD fallback）
    from app.database.jd_summary_repository import (
        JdDailySummaryRepository,
        JdSkillCandidateRepository,
    )
    from app.services.jd_summary_service import JdSummaryService as _JdSumSvc
    from app.services.market_signal import MarketSignal

    jd_summary_service = _JdSumSvc(
        JdDailySummaryRepository(conn),
        skill_repo,
        candidate_repo=JdSkillCandidateRepository(conn),
        route_repo=route_repo,
    )
    skill_service.market_signal = MarketSignal(jd_summary_service)
    # stage alignment 排序偏好：当前 phase 有对应 topic 的技能优先
    skill_service.current_phase_provider = (
        lambda: study_plan_service.get_current_phase(today())
    )
    # 从 career_context 幂等同步正式 skill（存量库补缺；不覆盖已维护状态）
    try:
        skills_sync = skill_service.sync_skills_from_career_context()
        if skills_sync.get("added"):
            print(f"[startup] 已补入技能：{'、'.join(skills_sync['added'])}")
    except Exception:  # noqa: BLE001
        pass
    # 幂等补齐 skill→topic 映射（存量库修复；不删除历史 link）
    try:
        sync = skill_service.sync_skill_topic_links()
        if sync.get("added_count"):
            print(f"[startup] 已补齐 {sync['added_count']} 条技能-主题映射")
    except Exception:  # noqa: BLE001
        pass

    # Phase 1：canonical 六技术路线 seed + 安全历史迁移 + skill→route 映射
    try:
        canonical_result = CanonicalRouteService(
            conn, route_repo, plan_repo, skill_repo,
            topic_learning_service=topic_learning_service,
        ).ensure_all()
        mig = canonical_result.get("migration") or {}
        print(
            "[startup] canonical routes seeded: "
            f"routes={len(canonical_result.get('route_ids') or {})} "
            f"route_skills=+{canonical_result.get('route_skill_links')} "
            f"topic_links=+{canonical_result.get('topic_skill_links')} "
            f"migration_applied={mig.get('applied')} "
            f"reason={mig.get('reason')} "
            f"summary={mig.get('summary')}"
        )
    except Exception as e:  # noqa: BLE001 - seed/迁移失败不阻止启动
        print(f"[startup] canonical routes seed 失败：{e}")

    # Phase 3：capability evidence 幂等 backfill（只读已有真实证据，不碰 mastery）
    try:
        cap_stats = capability_service.backfill()
        print(f"[startup] capability backfill: {cap_stats}")
    except Exception as e:  # noqa: BLE001 - backfill 失败不阻止启动
        print(f"[startup] capability backfill 失败：{e}")

    try:
        skill_service.refresh_market()
        skill_service.recompute_all_priority_scores()
    except Exception:  # noqa: BLE001
        pass
    # 历史未匹配 JD 技能修复（明确 alias 生效） + 刷新 JD 新技能候选池
    try:
        repair = jd_summary_service.repair_unmatched_jd_skills()
        if repair.get("repaired"):
            print(
                f"[startup] 已修复 {repair['repaired']} 条 JD 未匹配技能映射"
            )
        jd_summary_service.refresh_candidates(today())
    except Exception:  # noqa: BLE001 - 修复失败不影响启动
        pass

    # 存量“生成型新任务”description 回填（一次性、幂等；失败不阻止启动）
    try:
        repaired = study_plan_service.repair_existing_task_descriptions()
        if repaired:
            print(f"[startup] 已回填 {repaired} 条生成型任务的学习内容")
    except Exception:  # noqa: BLE001 - 回填失败不影响启动
        pass

    # 存量“生成型新任务”topic -> knowledge_point 关联修复（一次性、幂等；
    # 只补关系、不补证据；失败不阻止启动）
    try:
        kp_result = study_plan_service.repair_task_knowledge_points()
        print(
            "[startup] 知识点关联修复：repaired={repaired} skipped={skipped} "
            "error={error}".format(**kp_result)
        )
    except Exception:  # noqa: BLE001 - 修复失败不影响启动
        pass

    # 技能同步已在 ensure_default_plan 之后完成（见上）；此处不再按“空池”条件 seed。

    # AI 配置读取环境变量；未配置时 GUI 正常运行（本地功能不受影响）
    # AI 设置中心：Profile 存 SQLite（不含 Key），Key 存系统 keyring。
    # AdaptiveAIClient 在每次请求解析当前配置 → 切换 / 修改后无需重启。
    ai_config_service = AIConfigService(db_path=str(resolve_db_path()))
    prompt_registry = PromptRegistry(PromptOverrideRepository(conn))
    ai_client = AdaptiveAIClient(ai_config_service.get_runtime_config)
    outcome_service.ai_client = ai_client  # 简历素材的 AI 组织（可选）
    # 学习成果服务支持 Prompt 覆盖
    outcome_service.prompt_registry = prompt_registry
    from app.services.jd_service import JdService, build_default_parse_ai

    jd_service = JdService(
        JdRepository(conn), skill_repo, skill_service, ai_client=ai_client
    )
    # UI 里“分析 / 预览”也支持 AI 结构化解析（可选增强；失败回退规则）
    jd_service.parse_ai = (
        build_default_parse_ai(ai_client, prompt_registry)
        if ai_client.is_configured() else None
    )
    # Step 5/6：每日 JD 技术汇总（市场样本）已在上方构造
    # 长期学习上下文（职业目标/JD/技能路线/能力状态）：作为 AI 规划的长期依据；
    # 文件缺失/非法时返回 None，Planner 自动降级为旧行为，不影响启动。
    long_term_context = load_long_term_context()
    daily_planner = DailyPlannerService(
        repo,
        plan_repo,
        planner=AIPlanner(
            ai_client,
            long_term_context=long_term_context,
            prompt_registry=prompt_registry,
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
    # Phase D：多路线全局 Scheduler（共享 Global Agent Daily Budget）
    from app.services.route_scheduler import GlobalDailyScheduler

    scheduler = GlobalDailyScheduler(
        repo,
        plan_repo,
        route_repo,
        assessment_repo=assessment_repo,
        skill_service=skill_service,
        jd_service=jd_service,
        planner=daily_planner.planner,
        topic_learning_service=topic_learning_service,
        feedback_service=planner_feedback_service,
    )
    date_service.scheduler = scheduler
    review_service = TaskReviewService(ai_client, prompt_registry=prompt_registry)

    # Phase 3D~6：验收 / 复习调度
    from app.services.assessment_service import AssessmentService
    from app.services.review_service import ReviewService

    # 复习调度（依赖 TaskRepository + AssessmentRepository）
    review_scheduler = ReviewService(repo, assessment_repo, plan_repo=plan_repo)
    # 验收（判题成功后自动联动复习调度）
    assessment_service = AssessmentService(
        ai_client,
        assessment_repo=assessment_repo,
        review_service=review_scheduler,
        outcome_service=outcome_service,
        prompt_registry=prompt_registry,
        capability_service=capability_service,
    )

    # 验收后台线程专用：为 worker 的“独立连接”构造一套同配置依赖，
    # 避免把主线程 sqlite 连接传入子线程（SQLite thread affinity）。
    def build_assessment_service(fresh_conn):
        fresh_repo = TaskRepository(fresh_conn)
        fresh_plan_repo = StudyPlanRepository(fresh_conn)
        fresh_assessment_repo = AssessmentRepository(fresh_conn)
        fresh_review = ReviewService(
            fresh_repo, fresh_assessment_repo, plan_repo=fresh_plan_repo
        )
        fresh_outcome = LearningOutcomeService(
            LearningOutcomeRepository(fresh_conn)
        )
        fresh_outcome.ai_client = ai_client
        fresh_outcome.prompt_registry = prompt_registry
        from app.database.capability_repository import (
            CapabilityEvidenceRepository,
        )
        from app.services.capability_service import CapabilityService

        fresh_capability = CapabilityService(
            fresh_conn, CapabilityEvidenceRepository(fresh_conn)
        )
        fresh_outcome.capability_service = fresh_capability
        return AssessmentService(
            ai_client,
            assessment_repo=fresh_assessment_repo,
            review_service=fresh_review,
            outcome_service=fresh_outcome,
            prompt_registry=prompt_registry,
            capability_service=fresh_capability,
        )
    # Phase A：手动添加今日学习任务（普通 To-do / 正式知识任务）
    from app.services.manual_task_service import ManualTaskService

    manual_task_service = ManualTaskService(
        repo,
        assessment_repo=assessment_repo,
        study_plan_service=study_plan_service,
        topic_learning_service=topic_learning_service,
    )

    # 周/月总结（本地统计 + AI 解读 + 缓存）
    summary_service = SummaryService(
        stats_service=StatsService(repo),
        cache_repo=SummaryCacheRepository(conn),
        ai_generator=AISummaryGenerator(ai_client, prompt_registry=prompt_registry),
        route_progress_service=route_progress_service,
    )

    # Phase F：AI 学习路线草稿（纯 AI，不碰 DB）
    from app.services.ai_route_service import AIRouteBuilderService

    ai_route_service = AIRouteBuilderService(
        ai_client, prompt_registry=prompt_registry
    )

    # AI 设置 · Prompt 最终预览（真实当前数据）
    from app.services.prompt_preview_service import PromptPreviewService

    prompt_preview_service = PromptPreviewService(
        prompt_registry,
        today_provider=today,
        daily_planner_service=daily_planner,
        scheduler=scheduler,
        route_repo=route_repo,
        summary_service=summary_service,
        assessment_repo=assessment_repo,
        outcome_service=outcome_service,
        task_repo=repo,
        jd_repo=JdRepository(conn),
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

    # 跨日未确认正式任务：在“自动归档 + 生成今日计划”之前必须先补确认。
    # 若用户未确认（取消 / X / Esc）或写入失败，则本次启动终止，不进入今日学习。
    if not _run_past_task_preflight(repo, task_service, today()):
        return 0

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
        skill_service=skill_service,
        jd_service=jd_service,
        jd_summary_service=jd_summary_service,
        outcome_service=outcome_service,
        notes_service=notes_service,
        manual_task_service=manual_task_service,
        route_service=route_service,
        route_plan_service=route_plan_service,
        route_progress_service=route_progress_service,
        ai_route_service=ai_route_service,
        scheduler=scheduler,
        capability_service=capability_service,
        # 验收后台线程：只传 db_path + 工厂（worker 内自建连接）
        assessment_service_factory=build_assessment_service,
        db_path=str(resolve_db_path()),
        ai_config_service=ai_config_service,
        prompt_registry=prompt_registry,
        prompt_preview_service=prompt_preview_service,
        topic_learning_service=topic_learning_service,
        practice_service=practice_service,
        practice_capability_service=practice_capability_service,
        practice_readiness_service=practice_readiness_service,
    )
    # 新实例启动请求 → 恢复/前置已有唯一实例（从托盘恢复或直接激活）
    if hasattr(window, "_restore_from_tray"):
        guard.restore_requested.connect(window._restore_from_tray)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
