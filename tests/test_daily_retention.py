"""每日巩固复习（Daily Retention）测试。

覆盖：补足规则、候选来源、近期优先、weak 优先、冷却、幂等、
与 mastery / review_schedule / assessment 的隔离、UI 标签。
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.ui.main_window import MainWindow
from app.ui.task_widget import TaskWidget
from app.services.review_service import RETENTION_SOURCE, ReviewService

TODAY = "2026-09-14"


def _arepo(conn):
    return AssessmentRepository(conn)


@pytest.fixture()
def arepo(conn):
    return AssessmentRepository(conn)


def _kp(arepo, name, **fields):
    kp = arepo.create_knowledge_point(name)
    if fields:
        kp = arepo.update_knowledge_point(kp["id"], **fields)
    return kp


def _learn(repo, kp_id, learned_date, title="learned", task_type="new",
           source="generated", done=True):
    t = repo.create(title=title, scheduled_date=learned_date, source=source,
                    task_type=task_type, knowledge_point_id=kp_id)
    if done:
        repo.mark_done(t.id)
    return repo.get(t.id)


def _due_kp(arepo, name, next_review_date="2026-09-06"):
    return _kp(arepo, name, last_assessed_at="2026-09-01T10:00:00",
               next_review_date=next_review_date, interval_days=2,
               mastery_estimate=0.5, review_count=1)


def _judge(arepo, kp_id, result_level):
    a = arepo.create_attempt(kp_id, "[]")
    return arepo.update_attempt(a["id"], result_level=result_level,
                                judge_status="judged")


def _seed_history(repo, arepo, dates=("2026-09-10", "2026-09-09",
                                      "2026-09-08")):
    out = []
    for i, d in enumerate(dates):
        kp = _kp(arepo, f"hist{kp_name_suffix(i)}")
        _learn(repo, kp["id"], d, title=f"学习 {kp['name']}")
        out.append(kp)
    return out


def kp_name_suffix(i):
    return f".{i}"


def _svc(repo, arepo):
    return ReviewService(repo, arepo)


def _review_tasks(repo, date=TODAY):
    return [t for t in repo.list_by_date(date) if t.task_type == "review"]


# ================= 补足规则 =================

class TestTopUp:
    def test_due_zero_with_history_creates_three(self, repo, arepo):
        _seed_history(repo, arepo)
        res = _svc(repo, arepo).generate_daily_retention_reviews(TODAY)
        assert res["due_count"] == 0
        assert len(res["created"]) == 3
        assert len(_review_tasks(repo)) == 3

    def test_due_one_tops_up_two(self, repo, arepo, conn):
        _due_kp(arepo, "due")
        _seed_history(repo, arepo, dates=("2026-09-10", "2026-09-09",
                                          "2026-09-08", "2026-09-07"))
        svc = _svc(repo, arepo)
        svc.generate_due_reviews(TODAY)
        res = svc.generate_daily_retention_reviews(TODAY)
        assert res["due_count"] == 1
        assert len(res["created"]) == 2

    def test_due_two_tops_up_one(self, repo, arepo):
        _due_kp(arepo, "due1")
        _due_kp(arepo, "due2")
        _seed_history(repo, arepo, dates=("2026-09-10", "2026-09-09",
                                          "2026-09-08"))
        svc = _svc(repo, arepo)
        svc.generate_due_reviews(TODAY)
        res = svc.generate_daily_retention_reviews(TODAY)
        assert res["due_count"] == 2
        assert len(res["created"]) == 1

    def test_due_at_or_above_target_no_topup(self, repo, arepo):
        for i in range(4):
            _due_kp(arepo, f"due{i}")
        svc = _svc(repo, arepo)
        svc.generate_due_reviews(TODAY)
        res = svc.generate_daily_retention_reviews(TODAY)
        assert res["due_count"] >= 3
        assert res["created"] == []
        assert len(_review_tasks(repo)) == 4

    def test_no_history_shows_empty(self, repo, arepo):
        res = _svc(repo, arepo).generate_daily_retention_reviews(TODAY)
        assert res["created"] == []
        assert res["candidates"] == []

    def test_total_never_exceeds_target(self, repo, arepo):
        _due_kp(arepo, "due1")
        _due_kp(arepo, "due2")
        _seed_history(repo, arepo)
        svc = _svc(repo, arepo)
        svc.generate_due_reviews(TODAY)
        svc.generate_daily_retention_reviews(TODAY)
        assert len(_review_tasks(repo)) == 3


# ================= 候选来源 =================

class TestCandidateSource:
    def test_active_new_not_included(self, repo, arepo):
        kp = _kp(arepo, "active")
        _learn(repo, kp["id"], "2026-09-10", done=False)
        assert _svc(repo, arepo).retention_candidates(TODAY) == []

    def test_extra_not_included(self, repo, arepo):
        kp = _kp(arepo, "extra")
        _learn(repo, kp["id"], "2026-09-10", task_type="extra", source="extra")
        assert _svc(repo, arepo).retention_candidates(TODAY) == []

    def test_manual_not_included(self, repo, arepo):
        kp = _kp(arepo, "manual")
        _learn(repo, kp["id"], "2026-09-10", source="manual")
        assert _svc(repo, arepo).retention_candidates(TODAY) == []

    def test_review_not_included(self, repo, arepo):
        kp = _kp(arepo, "review")
        _learn(repo, kp["id"], "2026-09-10", task_type="review", source="review")
        assert _svc(repo, arepo).retention_candidates(TODAY) == []

    def test_learning_without_kp_not_included(self, repo, arepo):
        repo.create(title="无 kp", scheduled_date="2026-09-10",
                    source="generated")
        t = repo.list_by_date("2026-09-10")[0]
        repo.mark_done(t.id)
        assert _svc(repo, arepo).retention_candidates(TODAY) == []


# ================= 排序 =================

class TestOrdering:
    def test_most_recent_learning_first(self, repo, arepo):
        old = _kp(arepo, "old")
        mid = _kp(arepo, "mid")
        new = _kp(arepo, "new")
        _learn(repo, old["id"], "2026-09-04")   # 10 天
        _learn(repo, mid["id"], "2026-09-09")   # 5 天
        _learn(repo, new["id"], "2026-09-12")   # 2 天
        cands = _svc(repo, arepo).retention_candidates(TODAY)
        assert [c["knowledge_point_id"] for c in cands] == [
            new["id"], mid["id"], old["id"]]

    def test_weak_evidence_first(self, repo, arepo):
        strong = _kp(arepo, "strong", last_assessed_at="2026-09-12T10:00:00",
                     mastery_estimate=0.9)
        _learn(repo, strong["id"], "2026-09-12")   # 更新
        _judge(arepo, strong["id"], "good")
        weak = _kp(arepo, "weak", last_assessed_at="2026-09-09T10:00:00",
                   mastery_estimate=0.3)
        _learn(repo, weak["id"], "2026-09-09")     # 较旧但更弱
        _judge(arepo, weak["id"], "poor")
        cands = _svc(repo, arepo).retention_candidates(TODAY)
        assert cands[0]["knowledge_point_id"] == weak["id"]
        assert cands[0]["weak"] is True
        assert cands[1]["weak"] is False

    def test_far_past_ranked_last(self, repo, arepo):
        recent = _kp(arepo, "recent")
        far = _kp(arepo, "far")
        _learn(repo, far["id"], "2026-08-20")   # 25 天
        _learn(repo, recent["id"], "2026-09-13")
        cands = _svc(repo, arepo).retention_candidates(TODAY)
        assert cands[0]["knowledge_point_id"] == recent["id"]
        assert cands[-1]["knowledge_point_id"] == far["id"]


# ================= 冷却 =================

class TestCooldown:
    def _retention_on(self, repo, kp_id, date):
        repo.create(title="每日巩固", scheduled_date=date,
                    source=RETENTION_SOURCE, task_type="review",
                    knowledge_point_id=kp_id)

    def test_cooldown_blocks_repeat(self, repo, arepo):
        kp = _kp(arepo, "cool")
        _learn(repo, kp["id"], "2026-09-10")
        self._retention_on(repo, kp["id"], "2026-09-13")  # 1 天前
        assert _svc(repo, arepo).retention_candidates(TODAY) == []

    def test_cooldown_allows_after_gap(self, repo, arepo):
        kp = _kp(arepo, "ok")
        _learn(repo, kp["id"], "2026-09-10")
        self._retention_on(repo, kp["id"], "2026-09-11")  # 3 天前
        cands = _svc(repo, arepo).retention_candidates(TODAY)
        assert [c["knowledge_point_id"] for c in cands] == [kp["id"]]

    def test_weak_topic_shorter_cooldown(self, repo, arepo):
        kp = _kp(arepo, "weak", last_assessed_at="2026-09-09T10:00:00",
                 mastery_estimate=0.2)
        _learn(repo, kp["id"], "2026-09-10")
        _judge(arepo, kp["id"], "poor")
        self._retention_on(repo, kp["id"], "2026-09-13")  # 1 天前
        # 弱项允许间隔 1 天 → 仍可入选
        cands = _svc(repo, arepo).retention_candidates(TODAY)
        assert [c["knowledge_point_id"] for c in cands] == [kp["id"]]

    def test_due_review_not_affected_by_cooldown(self, repo, arepo):
        kp = _due_kp(arepo, "due")
        self._retention_on(repo, kp["id"], "2026-09-13")
        svc = _svc(repo, arepo)
        res = svc.generate_due_reviews(TODAY)
        assert len(res["created"]) == 1  # 到期复习不受每日巩固冷却影响


# ================= 幂等 / 隔离 =================

class TestIdempotencyAndIsolation:
    def test_same_day_rerun_no_duplicate(self, repo, arepo):
        _seed_history(repo, arepo)
        svc = _svc(repo, arepo)
        first = svc.generate_daily_retention_reviews(TODAY)
        second = svc.generate_daily_retention_reviews(TODAY)
        assert len(first["created"]) == 3
        assert second["created"] == []
        assert len(_review_tasks(repo)) == 3

    def test_no_review_schedule_created(self, repo, arepo):
        _seed_history(repo, arepo)
        res = _svc(repo, arepo).generate_daily_retention_reviews(TODAY)
        for c in res["created"]:
            assert arepo.list_review_schedules_for_kp(
                c["knowledge_point_id"]) == []
            assert arepo.find_pending_review_for_task(c["task_id"]) is None

    def test_no_assessment_attempt_created(self, repo, arepo):
        _seed_history(repo, arepo)
        _svc(repo, arepo).generate_daily_retention_reviews(TODAY)
        assert arepo.list_attempts() == []

    def test_retention_task_is_review_type(self, repo, arepo):
        _seed_history(repo, arepo)
        _svc(repo, arepo).generate_daily_retention_reviews(TODAY)
        tasks = _review_tasks(repo)
        assert tasks and all(t.source == RETENTION_SOURCE for t in tasks)
        assert all(t.estimated_minutes <= 20 for t in tasks)

    def test_description_has_review_sections(self, repo, arepo):
        kp = _kp(arepo, "Function Calling / Tool Calling")
        _learn(repo, kp["id"], "2026-09-10")
        _svc(repo, arepo).generate_daily_retention_reviews(TODAY)
        desc = _review_tasks(repo)[0].description
        for section in ("【复习目标】", "【快速回忆】", "【最小实践】",
                        "【完成标准】"):
            assert section in desc
        assert desc.strip() != "Function Calling / Tool Calling"

    def test_completing_retention_keeps_mastery_and_schedule(self, repo, arepo,
                                                            conn):
        kp = _kp(arepo, "stable", last_assessed_at="2026-09-01T10:00:00",
                 next_review_date="2026-09-25", interval_days=7,
                 mastery_estimate=0.42)
        _learn(repo, kp["id"], "2026-09-10")
        svc = _svc(repo, arepo)
        res = svc.generate_daily_retention_reviews(TODAY)
        task_id = res["created"][0]["task_id"]
        repo.mark_done(task_id)  # “复习完成”
        after = arepo.get_knowledge_point(kp["id"])
        assert after["mastery_estimate"] == 0.42
        assert after["next_review_date"] == "2026-09-25"
        assert after["interval_days"] == 7
        assert arepo.list_attempts() == []
        assert after["review_count"] == kp["review_count"]


# ================= 与到期复习共存 =================

class TestDueReviewNotRegressed:
    def test_due_review_still_creates_schedule_and_source(self, repo, arepo):
        kp = _due_kp(arepo, "due")
        res = _svc(repo, arepo).generate_due_reviews(TODAY)
        assert len(res["created"]) == 1
        task = repo.get(res["created"][0]["task_id"])
        assert task.source == "review"
        assert task.task_type == "review"
        assert arepo.find_pending_review_for_task(task.id) is not None

    def test_due_preferred_over_retention(self, repo, arepo):
        _due_kp(arepo, "d1")
        _due_kp(arepo, "d2")
        _seed_history(repo, arepo)
        svc = _svc(repo, arepo)
        svc.generate_due_reviews(TODAY)
        svc.generate_daily_retention_reviews(TODAY)
        sources = [t.source for t in _review_tasks(repo)]
        assert sources.count("review") == 2
        assert sources.count(RETENTION_SOURCE) == 1


# ================= UI =================

class TestUi:
    def test_daily_retention_tag(self, qtbot, repo, arepo):
        kp = _kp(arepo, "x")
        t = repo.create(title="每日巩固 x", scheduled_date=TODAY,
                        source=RETENTION_SOURCE, task_type="review",
                        knowledge_point_id=kp["id"])
        w = TaskWidget(repo.get(t.id))
        qtbot.addWidget(w)
        assert w.review_tag_label.text() == "【每日巩固】"
        assert w.review_tag_label.isHidden() is False

    def test_due_review_tag(self, qtbot, repo, arepo):
        kp = _kp(arepo, "y")
        t = repo.create(title="复习 y", scheduled_date=TODAY, source="review",
                        task_type="review", knowledge_point_id=kp["id"])
        w = TaskWidget(repo.get(t.id))
        qtbot.addWidget(w)
        assert w.review_tag_label.text() == "【到期复习】"

    def test_new_task_has_no_review_tag(self, qtbot, repo):
        t = repo.create(title="新知识", scheduled_date=TODAY)
        w = TaskWidget(repo.get(t.id))
        qtbot.addWidget(w)
        assert w.review_tag_label.isHidden() is True

    def test_window_shows_daily_retention(self, qtbot, repo, task_service,
                                          date_service, conn):
        arepo = _arepo(conn)
        kp = _kp(arepo, "z")
        _learn(repo, kp["id"], "2026-09-10", title="学习 z")
        w = MainWindow(
            task_service=task_service,
            date_service=date_service,
            today_provider=lambda: TODAY,
            review_scheduler=_svc(repo, arepo),
            assessment_repo=arepo,
        )
        qtbot.addWidget(w)
        labels = [lbl.text() for lbl in w.list_container.findChildren(QLabel)]
        assert "今日复习" in labels
        assert "【每日巩固】" in labels
        assert "暂无可复习内容" not in labels

    def test_window_empty_state_without_history(self, qtbot, repo, task_service,
                                                date_service, conn):
        arepo = _arepo(conn)
        task_service.create_task("只有新知识", scheduled_date=TODAY)
        w = MainWindow(
            task_service=task_service,
            date_service=date_service,
            today_provider=lambda: TODAY,
            review_scheduler=_svc(repo, arepo),
            assessment_repo=arepo,
        )
        qtbot.addWidget(w)
        labels = [lbl.text() for lbl in w.list_container.findChildren(QLabel)]
        assert "暂无可复习内容" in labels
