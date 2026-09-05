"""命令行 --date 注入入口测试（仅开发/测试用）。

覆盖：
- --date 2026-09-05 正确解析并注入“今天”
- 非法/不存在的日期被清晰拒绝并退出
- 不传参数使用系统真实日期（原行为不变）
- 注入后 GUI 使用该日期
- Qt 参数（如 -platform offscreen）不受影响
"""

from __future__ import annotations

import datetime

import pytest

from app.main import parse_date_arg
from app.utils import date_utils

D9_05 = "2026-09-05"


class TestParseDateArg:
    def test_no_arg_returns_none(self):
        """不传 --date：返回 None，且没有需要剔除的 Qt 参数。"""
        date_arg, remaining = parse_date_arg([])
        assert date_arg is None
        assert remaining == []

    def test_inject_09_05(self):
        """--date 2026-09-05：注入正确，且 --date 及其值不留入 Qt 参数。"""
        date_arg, remaining = parse_date_arg(["--date", D9_05])
        assert date_arg == D9_05
        assert remaining == []

    def test_inject_equals_form(self):
        date_arg, _ = parse_date_arg(["--date=" + D9_05])
        assert date_arg == D9_05

    def test_qt_args_preserved(self):
        date_arg, remaining = parse_date_arg(
            ["--date", D9_05, "-platform", "offscreen"]
        )
        assert date_arg == D9_05
        assert remaining == ["-platform", "offscreen"]

    def test_invalid_format_rejected(self):
        """非法格式（如 DD-MM-YYYY / 非补零写法）被拒绝并退出。"""
        for bad in ("09-05-2026", "2026-9-5", "20260905", "abc"):
            with pytest.raises(SystemExit):
                parse_date_arg(["--date", bad])

    def test_nonexistent_date_rejected_with_clear_error(self, capsys):
        """不存在的日期（2026-02-30）被拒绝，错误信息包含原值。"""
        with pytest.raises(SystemExit):
            parse_date_arg(["--date", "2026-02-30"])
        assert "2026-02-30" in capsys.readouterr().err

    def test_missing_value_rejected(self):
        with pytest.raises(SystemExit):
            parse_date_arg(["--date"])


class TestInjectionBehavior:
    def test_no_arg_uses_real_today(self):
        """不传参数：不注入，today() 即系统当前日期（原行为）。"""
        date_arg, _ = parse_date_arg([])
        assert date_arg is None
        assert date_utils.today() == datetime.date.today().strftime("%Y-%m-%d")

    def test_date_arg_injects_today_and_resets(self):
        """main() 的注入分支：--date 生效期间 today()==09-05，之后恢复。"""
        date_arg, _ = parse_date_arg(["--date", D9_05])
        original = date_utils.today()
        try:
            date_utils.set_today_provider(date_arg)
            assert date_utils.today() == D9_05
        finally:
            date_utils.reset_today_provider()
            assert date_utils.today() == original

    def test_window_uses_injected_date(self, qtbot, repo, date_service, task_service):
        """注入 09-05 后，GUI（不显式传 today_provider）显示该日期。"""
        from app.ui.main_window import MainWindow

        date_arg, _ = parse_date_arg(["--date", D9_05])
        original = date_utils.today()
        try:
            date_utils.set_today_provider(date_arg)
            w = MainWindow(task_service=task_service, date_service=date_service)
            qtbot.addWidget(w)
            assert w.date_label.text() == D9_05
            assert w.current_date == D9_05
        finally:
            date_utils.reset_today_provider()
            assert date_utils.today() == original


class TestEndToEndMain:
    """main() 注入链路：--date 在窗口构造前生效，且不会污染 Qt 参数。"""

    def test_main_injects_date_and_filters_qt_args(self, monkeypatch, tmp_path):
        from app import main as main_module
        from app.database.connection import get_connection as _get_connection

        import sys as _sys

        captured = {}

        class FakeApp:
            def __init__(self, argv):
                captured["argv"] = argv

            def setApplicationName(self, name):
                captured["app_name"] = name

            def exec(self):
                captured["exec"] = True
                return 0

        class SpyWindow:
            def __init__(self, **kwargs):
                captured["window_kwargs"] = kwargs
                # 记录窗口构造时刻的“今天”，验证注入早于 UI 组装
                captured["today_at_construct"] = date_utils.today()

            def show(self):
                captured["shown"] = True

        original = date_utils.today()
        try:
            monkeypatch.setattr(main_module, "QApplication", FakeApp)
            monkeypatch.setattr(main_module, "MainWindow", SpyWindow)
            tmp_db = str(tmp_path / "main.db")
            monkeypatch.setattr(
                main_module, "get_connection", lambda: _get_connection(tmp_db)
            )
            monkeypatch.setattr(
                _sys, "argv", ["app/main.py", "--date", D9_05]
            )
            code = main_module.main()

            assert code == 0
            assert captured["exec"] is True
            assert captured["shown"] is True
            # 注入在窗口构造先发生
            assert captured["today_at_construct"] == D9_05
            # --date 及其取值不会泄漏给 Qt
            assert captured["argv"] == ["app/main.py"]
            assert "--date" not in captured["argv"]
            assert D9_05 not in captured["argv"]
        finally:
            date_utils.reset_today_provider()
            assert date_utils.today() == original


@pytest.fixture()
def plan_repo(repo):
    from app.database.study_plan_repository import StudyPlanRepository

    return StudyPlanRepository(repo.conn)


class TestWholeAppUsesInjectedDate:
    """09-05 注入后，整个应用各环节统一使用注入日期。"""

    def test_gui_date_label(self, qtbot, repo, date_service, task_service):
        from app.ui.main_window import MainWindow

        original = date_utils.today()
        try:
            date_utils.set_today_provider(D9_05)
            w = MainWindow(task_service=task_service, date_service=date_service)
            qtbot.addWidget(w)
            assert w.date_label.text() == D9_05
        finally:
            date_utils.reset_today_provider()
            assert date_utils.today() == original

    def test_current_phase_judged_by_09_05(self, qtbot, repo, plan_repo):
        """当前 Phase 依据注入的 09-05 判断并显示。"""
        from app.services.date_service import DateService
        from app.services.study_plan_service import StudyPlanService
        from app.services.task_service import TaskService
        from app.ui.main_window import MainWindow

        sps = StudyPlanService(repo, plan_repo)
        sps.ensure_default_plan()
        phase = sps.get_current_phase(D9_05)
        assert phase is not None

        original = date_utils.today()
        try:
            date_utils.set_today_provider(D9_05)
            w = MainWindow(
                task_service=TaskService(repo),
                date_service=DateService(repo),
                study_plan_service=sps,
            )
            qtbot.addWidget(w)
            assert w.current_date == D9_05
            assert f"当前阶段：{phase.name}" in w.phase_label.text()
        finally:
            date_utils.reset_today_provider()
            assert date_utils.today() == original

    def test_today_task_query_uses_09_05(self, repo):
        from app.services.task_service import TaskService

        ts = TaskService(repo)
        original = date_utils.today()
        try:
            date_utils.set_today_provider(D9_05)
            t = ts.create_task("今日任务")
            assert t.scheduled_date == D9_05
            assert any(x.id == t.id for x in ts.get_tasks_by_date(date_utils.today()))
        finally:
            date_utils.reset_today_provider()
            assert date_utils.today() == original

    def test_stats_uses_09_05(self, repo):
        from app.services.stats_service import StatsService

        svc = StatsService(repo)
        original = date_utils.today()
        try:
            date_utils.set_today_provider(D9_05)
            trend = svc.get_learning_trend(days=3)
            assert trend["points"][-1]["date"] == D9_05
        finally:
            date_utils.reset_today_provider()
            assert date_utils.today() == original

    def test_date_service_uses_09_05(self, repo):
        from app.services.date_service import DateService

        ds = DateService(repo)
        original = date_utils.today()
        try:
            date_utils.set_today_provider(D9_05)
            res = ds.process_date_transition(date_utils.today())
            assert res["current_date"] == D9_05
            assert ds.get_last_processed_date() == D9_05
        finally:
            date_utils.reset_today_provider()
            assert date_utils.today() == original

    def test_cli_date_not_overridden_by_db_last_processed(self, qtbot, repo):
        """即使 SQLite 已有 last_processed_date，--date 注入的今天仍然生效。"""
        from app.services.date_service import DateService
        from app.services.task_service import TaskService
        from app.ui.main_window import MainWindow

        repo.set_meta("last_processed_date", "2026-09-03")
        original = date_utils.today()
        try:
            date_utils.set_today_provider(D9_05)
            ds = DateService(repo)
            res = ds.process_date_transition(date_utils.today())
            assert res["current_date"] == D9_05
            assert ds.get_last_processed_date() == D9_05

            w = MainWindow(task_service=TaskService(repo), date_service=ds)
            qtbot.addWidget(w)
            assert w.date_label.text() == D9_05
        finally:
            date_utils.reset_today_provider()
            assert date_utils.today() == original

    def test_no_arg_still_system_date(self):
        """不传 --date：统一使用系统真实日期（原行为）。"""
        assert date_utils.today() == datetime.date.today().strftime("%Y-%m-%d")
