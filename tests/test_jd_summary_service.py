"""Step 4：每日 JD 技术汇总（数据层 + Service）测试。

覆盖：migration/幂等/历史保留、解析格式、alias/未匹配、preview 不写库、
保存与同日幂等 replace、事务原子性、14/30 天窗口与频率、target 隔离、
排序、raw 名保留、must/plus、旧 individual JD 保留、不触碰其它功能。
"""

from __future__ import annotations

import pytest

from app.database.connection import get_connection
from app.database.jd_summary_repository import JdDailySummaryRepository
from app.database.schema import SCHEMA_VERSION, get_schema_version, migrate
from app.database.skill_repository import JdRepository, SkillRepository
from app.services.jd_summary_service import JdSummaryService

TARGET = "internship"


def _skills(conn) -> SkillRepository:
    sr = SkillRepository(conn)
    for name in ("Python", "PyTorch", "Transformer", "Hugging Face",
                 "LLM 基础", "Embedding", "Recall", "Ranking", "RAG",
                 "推荐系统基础"):
        sr.upsert_by_name(name)
    return sr


@pytest.fixture()
def svc_env(conn):
    sr = _skills(conn)
    repo = JdDailySummaryRepository(conn)
    return {"conn": conn, "sr": sr, "repo": repo,
            "svc": JdSummaryService(repo, sr)}


DAY1 = "2026-09-12"
DAY2 = "2026-09-13"
DAY3 = "2026-09-14"

T1 = "Python 8\nPyTorch 7\nEmbedding 4\nRecall 3"
T2 = ("Python 13\nPyTorch 11\nEmbedding 9\nRecall 8\nRanking 7")
T3 = ("Python 10\nPyTorch 9\nEmbedding 8\nRecall 7\nRanking 6\nRAG 4")


# ================= 1~3：migration =================

class TestMigration:
    def test_v8_to_v9_and_history_preserved(self, conn):
        # 历史 individual JD 必须保留
        jd = JdRepository(conn)
        jd.create(company="A", title="t", direction="rec", raw_text="x",
                  parsed={"must": ["Python"], "plus": []},
                  content_hash="h1")
        before = len(jd.list_all())
        assert get_schema_version(conn) == SCHEMA_VERSION == 9
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "jd_daily_summaries" in tables
        assert "jd_daily_skill_stats" in tables
        assert len(jd.list_all()) == before  # 历史 JD 未被破坏

    def test_migration_idempotent(self, conn):
        assert migrate(conn) == SCHEMA_VERSION
        assert migrate(conn) == SCHEMA_VERSION

    def test_old_individual_jd_not_deleted_by_use(self, svc_env):
        conn = svc_env["conn"]
        env = svc_env
        jd = JdRepository(conn)
        jd.create(company="A", title="t", direction="rec", raw_text="x",
                  parsed={"must": ["Python"]}, content_hash="h9")
        env["svc"].save_summary(DAY1, T1, 10, TARGET)
        env["svc"].compute_skill_trends(DAY1, 14, TARGET)
        assert len(jd.list_all()) == 1


# ================= 4~6：创建 / 校验 =================

class TestCreateAndValidate:
    def test_create_summary(self, svc_env):
        env = svc_env
        row = env["svc"].save_summary(DAY1, T1, 10, TARGET)
        assert row["summary_date"] == DAY1
        assert row["target_type"] == TARGET
        assert row["sample_count"] == 10
        assert len(row["stats"]) == 4

    def test_sample_count_must_be_positive(self, svc_env):
        env = svc_env
        p = env["svc"].preview_summary(T1, 0, TARGET, DAY1)
        assert not p["valid"]
        assert any("sample_count" in e for e in p["errors"])
        with pytest.raises(ValueError):
            env["svc"].save_summary(DAY1, T1, 0, TARGET)

    def test_mention_over_sample_rejected_not_truncated(self, svc_env):
        env = svc_env
        p = env["svc"].preview_summary("Python 18", 15, TARGET, DAY1)
        assert not p["valid"]
        assert any("超过 sample_count" in e for e in p["errors"])
        with pytest.raises(ValueError):
            env["svc"].save_summary(DAY1, "Python 18", 15, TARGET)
        assert env["repo"].get_summary(DAY1, TARGET) is None  # 未写入

    def test_must_plus_over_mention_rejected(self, svc_env):
        env = svc_env
        p = env["svc"].preview_summary("Python 10 must=8 plus=5", 15, TARGET, DAY1)
        assert not p["valid"]
        assert any("超过 mention_count" in e for e in p["errors"])


# ================= 7~11：解析 / alias / unmatched =================

class TestParsing:
    def test_space_and_colon_formats(self, svc_env):
        env = svc_env
        entries, errors = env["svc"].parse_summary_text(
            "Python 13\nPyTorch: 11\nEmbedding：9")
        assert errors == []
        assert [(e["raw_skill_name"], e["mention_count"]) for e in entries] == [
            ("Python", 13), ("PyTorch", 11), ("Embedding", 9)]

    def test_must_plus_and_slash_format(self, svc_env):
        env = svc_env
        entries, errors = env["svc"].parse_summary_text(
            "Python must=10 plus=2\nRecall 8/15\nC++ 2")
        assert errors == []
        by = {e["raw_skill_name"]: e for e in entries}
        assert by["Python"]["mention_count"] == 12
        assert by["Python"]["must_count"] == 10
        assert by["Python"]["plus_count"] == 2
        assert by["Recall"]["mention_count"] == 8
        assert by["C++"]["mention_count"] == 2

    def test_alias_mapping(self, svc_env):
        env = svc_env
        p = env["svc"].preview_summary("HF 3\n召回 2\nRecSys 1", 10, TARGET, DAY1)
        names = {r["name"] for r in p["matched"]}
        assert {"Hugging Face", "Recall", "推荐系统基础"} <= names
        assert p["unmatched"] == []

    def test_unmatched_preserved(self, svc_env):
        env = svc_env
        p = env["svc"].preview_summary("Two-Tower 6", 15, TARGET, DAY1)
        assert len(p["unmatched"]) == 1
        u = p["unmatched"][0]
        assert u["raw_skill_name"] == "Two-Tower" and u["skill_id"] is None
        row = env["svc"].save_summary(DAY1, "Two-Tower 6", 15, TARGET)
        stat = next(s for s in row["stats"] if s["raw_skill_name"] == "Two-Tower")
        assert stat["skill_id"] is None and stat["mention_count"] == 6

    def test_no_fuzzy_matching(self, svc_env):
        env = svc_env
        p = env["svc"].preview_summary("Pythonn 3\nRankingg 2", 10, TARGET, DAY1)
        assert p["matched"] == []
        assert {u["raw_skill_name"] for u in p["unmatched"]} == {
            "Pythonn", "Rankingg"}

    def test_preview_does_not_write(self, svc_env):
        env = svc_env
        env["svc"].preview_summary(T1, 10, TARGET, DAY1)
        assert env["repo"].get_summary(DAY1, TARGET) is None


# ================= 12~15：保存 / 幂等 / 事务 =================

class TestSave:
    def test_save_writes(self, svc_env):
        env = svc_env
        env["svc"].save_summary(DAY1, T1, 10, TARGET)
        got = env["repo"].get_summary(DAY1, TARGET)
        assert got is not None and len(got["stats"]) == 4

    def test_same_day_replace_not_accumulate(self, svc_env):
        env = svc_env
        env["svc"].save_summary(DAY1, "Python 10\nPyTorch 8", 15, TARGET)
        env["svc"].save_summary(DAY1, "Python 12\nPyTorch 9", 15, TARGET)
        rows = env["repo"].list_summaries(target_type=TARGET)
        assert len(rows) == 1  # 同日同目标只有一份
        stats = {s["raw_skill_name"]: s["mention_count"]
                 for s in rows[0]["stats"]}
        assert stats == {"Python": 12, "PyTorch": 9}  # 不是 22 / 17

    def test_upsert_is_atomic_on_failure(self, svc_env):
        env = svc_env
        env["svc"].save_summary(DAY1, "Python 10", 10, TARGET)
        before = env["repo"].get_summary(DAY1, TARGET)
        with pytest.raises((ValueError, TypeError)):
            env["repo"].upsert_summary(
                DAY1, TARGET, 20, raw_text="bad",
                stats=[{"mention_count": "not-an-int"}])
        after = env["repo"].get_summary(DAY1, TARGET)
        assert after["sample_count"] == before["sample_count"] == 10
        assert after["stats"] == before["stats"]
        assert len(env["repo"].list_summaries(target_type=TARGET)) == 1


# ================= 16~24：趋势 =================

def _seed_three_days(env):
    env["svc"].save_summary(DAY1, T1, 10, TARGET)
    env["svc"].save_summary(DAY2, T2, 15, TARGET)
    env["svc"].save_summary(DAY3, T3, 12, TARGET)


class TestTrends:
    def test_14day_window_and_frequency(self, svc_env):
        env = svc_env
        _seed_three_days(env)
        t = env["svc"].compute_skill_trends(DAY3, 14, TARGET)
        assert t["start_date"] == "2026-09-01"  # DAY3 - 13
        assert t["end_date"] == DAY3
        assert t["sample_count"] == 37
        by = {r["name"]: r for r in t["skills"]}
        assert by["Python"]["mention_count"] == 31
        assert by["Python"]["frequency"] == round(31 / 37, 4)
        assert by["PyTorch"]["mention_count"] == 27
        assert by["Embedding"]["mention_count"] == 21
        assert by["Recall"]["mention_count"] == 18
        assert by["Ranking"]["mention_count"] == 13
        assert by["RAG"]["mention_count"] == 4
        assert by["RAG"]["frequency"] == round(4 / 37, 4)

    def test_30day_window(self, svc_env):
        env = svc_env
        _seed_three_days(env)
        t = env["svc"].compute_skill_trends(DAY3, 30, TARGET)
        assert t["start_date"] == "2026-08-16"
        assert t["sample_count"] == 37  # 数据都在窗口内

    def test_window_boundary_inclusive(self, svc_env):
        env = svc_env
        env["svc"].save_summary("2026-09-01", "Python 5", 10, TARGET)  # 边界内
        env["svc"].save_summary("2026-08-31", "Python 9", 10, TARGET)  # 边界外
        t = env["svc"].compute_skill_trends(DAY3, 14, TARGET)
        assert t["start_date"] == "2026-09-01"
        assert t["sample_count"] == 10  # 只含边界内那天
        assert t["skills"][0]["mention_count"] == 5

    def test_target_type_isolation(self, svc_env):
        env = svc_env
        env["svc"].save_summary(DAY1, "Python 8", 10, "internship")
        env["svc"].save_summary(DAY1, "Python 2", 10, "fulltime")
        t = env["svc"].compute_skill_trends(DAY1, 14, "internship")
        assert t["sample_count"] == 10
        assert t["skills"][0]["mention_count"] == 8

    def test_multi_day_sample_sum(self, svc_env):
        env = svc_env
        env["svc"].save_summary(DAY1, "Python 8", 10, TARGET)
        env["svc"].save_summary(DAY2, "Python 12", 15, TARGET)
        env["svc"].save_summary(DAY3, "Python 3", 5, TARGET)
        t = env["svc"].compute_skill_trends(DAY3, 14, TARGET)
        assert t["sample_count"] == 30
        assert t["skills"][0]["mention_count"] == 23
        assert t["skills"][0]["frequency"] == round(23 / 30, 4)

    def test_skills_sorted_by_mention(self, svc_env):
        env = svc_env
        _seed_three_days(env)
        t = env["svc"].compute_skill_trends(DAY3, 14, TARGET)
        counts = [r["mention_count"] for r in t["skills"]]
        assert counts == sorted(counts, reverse=True)

    def test_unmatched_in_trends(self, svc_env):
        env = svc_env
        env["svc"].save_summary(DAY1, "Two-Tower 6", 10, TARGET)
        t = env["svc"].compute_skill_trends(DAY1, 14, TARGET)
        assert t["unmatched"][0]["name"] == "Two-Tower"
        assert t["unmatched"][0]["mention_count"] == 6

    def test_re_save_changes_window_total(self, svc_env):
        env = svc_env
        _seed_three_days(env)
        env["svc"].save_summary(DAY3, T3.replace("Python 10", "Python 11"),
                                12, TARGET)
        t = env["svc"].compute_skill_trends(DAY3, 14, TARGET)
        py = next(r for r in t["skills"] if r["name"] == "Python")
        assert t["sample_count"] == 37
        assert py["mention_count"] == 32  # 不是 41


# ================= 25~30：边界不变量 =================

class TestNoSideEffects:
    def test_no_task_or_planner_or_priority_change(self, svc_env):
        conn = svc_env["conn"]
        env = svc_env
        # 快照
        tasks_before = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        skills_before = {s["name"]: s["priority_score"]
                         for s in env["sr"].list_all()}
        env["svc"].save_summary(DAY1, T1, 10, TARGET)
        env["svc"].compute_skill_trends(DAY1, 14, TARGET)
        assert conn.execute(
            "SELECT COUNT(*) FROM tasks").fetchone()[0] == tasks_before
        skills_after = {s["name"]: s["priority_score"]
                        for s in env["sr"].list_all()}
        assert skills_before == skills_after  # 未改 priority
        # 未写 planner_decisions / 未建 review / 未建 assessment
        for table in ("planner_decisions", "review_schedule",
                      "assessment_attempts", "knowledge_points"):
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert n == 0, table
