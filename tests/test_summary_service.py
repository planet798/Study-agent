"""SummaryService 测试：本地统计 + 缓存 + AI fallback。"""

from __future__ import annotations

import json

import pytest

from app.ai.interface import AIServiceError
from app.ai.summary import AISummaryGenerator
from app.database.study_plan_repository import SummaryCacheRepository
from app.services.stats_service import StatsService
from app.services.summary_service import SummaryService
from app.services.task_service import TaskService


class FakeSummaryClient:
    def __init__(self, content=None, error=None, configured=True):
        self._content = content
        self._error = error
        self._configured = configured
        self.calls = 0

    def is_configured(self):
        return self._configured

    def chat(self, system_prompt, user_prompt, **kwargs):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._content


WEEKLY_JSON = json.dumps(
    {
        "overview": "本周完成良好",
        "strengths": ["坚持学习"],
        "problems": ["算法延期"],
        "recommendations": ["拆解任务"],
        "next_week_focus": ["加强算法"],
    },
    ensure_ascii=False,
)


@pytest.fixture()
def cache_repo(repo):
    return SummaryCacheRepository(repo.conn)


@pytest.fixture()
def stats(repo):
    return StatsService(repo)


class TestWeeklySummaryLocal:
    def test_weekly_local_stats_with_ai(self, repo, cache_repo):
        ts = TaskService(repo)
        t = ts.create_task("任务", scheduled_date="2026-01-05", estimated_minutes=30)
        ts.complete_task(t.id)

        client = FakeSummaryClient(content=WEEKLY_JSON)
        svc = SummaryService(
            StatsService(repo), cache_repo, AISummaryGenerator(client)
        )
        result = svc.get_weekly_summary("2026-01-05")
        assert result["source"] == "ai"
        assert result["stats"]["total_tasks"] == 1
        assert result["stats"]["completed_tasks"] == 1
        assert result["ai_summary"] is not None
        assert client.calls == 1

    def test_weekly_local_without_ai(self, repo, cache_repo):
        ts = TaskService(repo)
        ts.create_task("任务", scheduled_date="2026-01-05")
        svc = SummaryService(StatsService(repo), cache_repo, None)
        result = svc.get_weekly_summary("2026-01-05")
        assert result["source"] == "local"
        assert result["ai_summary"] is None
        assert result["stats"]["total_tasks"] == 1


class TestMonthlySummary:
    def test_monthly_local_stats(self, repo, cache_repo):
        ts = TaskService(repo)
        t = ts.create_task("任务", scheduled_date="2026-02-10", estimated_minutes=45)
        ts.complete_task(t.id)
        monthly_json = json.dumps(
            {
                "overview": "本月推进不错",
                "progress": "进入新阶段",
                "strengths": ["坚持性好"],
                "weaknesses": ["效率波动"],
                "recommendations": ["优化安排"],
                "next_month_focus": ["深入 PyTorch"],
            },
            ensure_ascii=False,
        )
        client = FakeSummaryClient(content=monthly_json)
        svc = SummaryService(StatsService(repo), cache_repo, AISummaryGenerator(client))
        result = svc.get_monthly_summary(2026, 2)
        assert result["stats"]["total_tasks"] == 1
        assert result["stats"]["completed_tasks"] == 1
        assert result["source"] == "ai"


class TestCache:
    def test_cache_hit(self, repo, cache_repo):
        ts = TaskService(repo)
        ts.create_task("任务", scheduled_date="2026-01-05")
        client = FakeSummaryClient(content=WEEKLY_JSON)
        svc = SummaryService(StatsService(repo), cache_repo, AISummaryGenerator(client))

        r1 = svc.get_weekly_summary("2026-01-05")
        assert client.calls == 1
        r2 = svc.get_weekly_summary("2026-01-05")
        assert client.calls == 1  # 未再调用 AI
        assert r2["cached"] is True
        assert r2["stats"] == r1["stats"]

    def test_cache_invalidated_when_stats_change(self, repo, cache_repo):
        ts = TaskService(repo)
        ts.create_task("旧任务", scheduled_date="2026-01-05")
        client = FakeSummaryClient(content=WEEKLY_JSON)
        svc = SummaryService(StatsService(repo), cache_repo, AISummaryGenerator(client))

        svc.get_weekly_summary("2026-01-05")
        assert client.calls == 1

        # 数据变化（新增任务）
        ts.create_task("新任务", scheduled_date="2026-01-05")
        r2 = svc.get_weekly_summary("2026-01-05")
        assert client.calls == 2  # 重新生成
        assert r2["cached"] is False
        assert r2["stats"]["total_tasks"] == 2

    def test_cache_upsert_same_period(self, repo, cache_repo):
        client = FakeSummaryClient(content=WEEKLY_JSON)
        svc = SummaryService(StatsService(repo), cache_repo, AISummaryGenerator(client))
        svc.get_weekly_summary("2026-01-05")
        svc.get_weekly_summary("2026-01-05")
        rows = cache_repo.conn.execute(
            "SELECT COUNT(*) AS n FROM weekly_summaries WHERE period_start='2026-01-05'"
        ).fetchone()
        assert rows["n"] == 1


class TestAiFailureFallback:
    def test_ai_error_still_local_stats(self, repo, cache_repo):
        ts = TaskService(repo)
        ts.create_task("任务", scheduled_date="2026-01-05")
        client = FakeSummaryClient(error=AIServiceError("AI 请求超时"))
        svc = SummaryService(StatsService(repo), cache_repo, AISummaryGenerator(client))
        result = svc.get_weekly_summary("2026-01-05")
        # 统计正常，AI 降级
        assert result["stats"]["total_tasks"] == 1
        assert result["ai_summary"] is None
        assert result["source"] == "local"

    def test_ai_not_configured_local(self, repo, cache_repo):
        ts = TaskService(repo)
        ts.create_task("任务", scheduled_date="2026-01-05")
        client = FakeSummaryClient(configured=False)
        svc = SummaryService(StatsService(repo), cache_repo, AISummaryGenerator(client))
        result = svc.get_weekly_summary("2026-01-05")
        assert result["ai_summary"] is None
        assert result["source"] == "local"
