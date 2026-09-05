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
from app.utils.date_utils import set_today_provider, to_date

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


def main() -> int:
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

    # 3) 组装依赖：SQLite -> Repository -> Service -> UI（UI 不直接碰 SQLite）
    conn = get_connection()
    repo = TaskRepository(conn)
    task_service = TaskService(repo)

    # 学习计划：确保默认研一计划已创建，供每日任务生成与阶段显示
    plan_repo = StudyPlanRepository(conn)
    study_plan_service = StudyPlanService(repo, plan_repo)
    study_plan_service.ensure_default_plan()

    # AI 配置读取环境变量；未配置时 GUI 正常运行（本地功能不受影响）
    ai_client = DeepSeekClient()
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
    )
    date_service = DateService(
        repo,
        study_plan_service=study_plan_service,
        daily_planner_service=daily_planner,
    )
    review_service = TaskReviewService(ai_client)

    # 周/月总结（本地统计 + AI 解读 + 缓存）
    summary_service = SummaryService(
        stats_service=StatsService(repo),
        cache_repo=SummaryCacheRepository(conn),
        ai_generator=AISummaryGenerator(ai_client),
    )

    # 不显式传 today_provider：MainWindow 默认跟随 date_utils.today()，
    # 因此 --date 注入的日期会自动作用于整个应用（GUI 日期/阶段/任务/统计/AI）。
    window = MainWindow(
        task_service=task_service,
        date_service=date_service,
        review_service=review_service,
        study_plan_service=study_plan_service,
        daily_planner_service=daily_planner,
        summary_service=summary_service,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
