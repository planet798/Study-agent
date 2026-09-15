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
from app.database.connection import get_connection, resolve_db_path
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

    dlg = PastTaskConfirmationDialog(unresolved)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False  # 取消 / X / Esc：不默认判未完成、不进入今天
    try:
        service.apply_decisions(dlg.result_decisions())
    except Exception as e:  # noqa: BLE001 - 更新失败则不继续启动
        show_warning(None, f"任务状态更新失败：{e}")
        return False
    return True


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
    review_scheduler = ReviewService(repo, assessment_repo, plan_repo=plan_repo)
    # 验收（判题成功后自动联动复习调度）
    assessment_service = AssessmentService(
        ai_client,
        assessment_repo=assessment_repo,
        review_service=review_scheduler,
        outcome_service=outcome_service,
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
        return AssessmentService(
            ai_client,
            assessment_repo=fresh_assessment_repo,
            review_service=fresh_review,
            outcome_service=fresh_outcome,
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
        extra_service=extra_service,
        exploration_service=exploration_service,
        skill_service=skill_service,
        jd_service=jd_service,
        jd_summary_service=jd_summary_service,
        outcome_service=outcome_service,
        notes_service=notes_service,
        # 验收后台线程：只传 db_path + 工厂（worker 内自建连接）
        assessment_service_factory=build_assessment_service,
        db_path=str(resolve_db_path()),
    )
    # 新实例启动请求 → 恢复/前置已有唯一实例（从托盘恢复或直接激活）
    if hasattr(window, "_restore_from_tray"):
        guard.restore_requested.connect(window._restore_from_tray)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
