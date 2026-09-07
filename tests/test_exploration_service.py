"""课外探索（Phase 6）测试。

覆盖：
- 默认资源池加载与有效性（URL/type 校验全部通过）
- 正常推荐（结果 URL 全部来自已验证资源）
- 按 topic / knowledge point 筛选
- weak point 优先
- URL 校验
- 非法资源在加载时被过滤
- 不可访问资源被剔除
- AI 不得生成未验证 URL（即使 AI 文本含伪造 URL，结果 url 仍来自资源文件）
- AI 失败回退、无匹配资源
- build_context 正确聚合当前状态
- 推荐不修改 tasks / study plan
"""

from __future__ import annotations

import json

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.ai.interface import AIServiceError
from app.services.exploration_service import (
    ALLOWED_RESOURCE_TYPES,
    ExplorationService,
    RESOURCE_FILE,
)


class FakeAI:
    def __init__(self, content=None, error=None, configured=True):
        self.content = content
        self.error = error
        self.configured = configured
        self.calls = []

    def is_configured(self):
        return self.configured

    def chat(self, system_prompt, user_prompt, **kwargs):
        self.calls.append((system_prompt, user_prompt))
        if self.error is not None:
            raise self.error
        return self.content


@pytest.fixture()
def svc():
    return ExplorationService()


@pytest.fixture()
def assessment_repo(conn):
    return AssessmentRepository(conn)


PYTORCH_CTX = {
    "phase": ["阶段二：深度学习与 LLM 基础"],
    "topics": ["PyTorch", "Transformer"],
    "knowledge_points": ["pytorch.autograd"],
    "weak_points": [],
}


class TestLoad:
    def test_default_resources_all_valid(self, svc):
        assert svc.resources, "默认资源池不应为空"
        for r in svc.resources:
            assert ExplorationService.valid_url(r["url"])
            assert r["type"] in ALLOWED_RESOURCE_TYPES
            assert r["id"] and r["title"]

    def test_missing_file_means_no_resources(self, tmp_path):
        e = ExplorationService(resources_path=tmp_path / "nope.json")
        assert e.resources == []
        assert e.load_error is not None

    def test_invalid_resources_filtered_on_load(self, tmp_path):
        data = {
            "resources": [
                {"id": "ok", "title": "好资源", "type": "github",
                 "url": "https://github.com/pytorch/pytorch", "why": "w",
                 "minutes": 10},
                {"id": "bad-url", "title": "坏链接", "type": "github",
                 "url": "github.com/pytorch/pytorch"},
                {"id": "bad-type", "title": "坏类型", "type": "ftp",
                 "url": "https://example.com/a"},
                {"title": "缺 id", "type": "docs",
                 "url": "https://example.com/b"},
            ]
        }
        p = tmp_path / "res.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        e = ExplorationService(resources_path=p)
        assert [r["id"] for r in e.resources] == ["ok"]


class TestUrlValidation:
    @pytest.mark.parametrize("ok", [
        "https://github.com/pytorch/pytorch",
        "http://pytorch.org/tutorials/",
    ])
    def test_valid(self, ok):
        assert ExplorationService.valid_url(ok)

    @pytest.mark.parametrize("bad", [
        "",
        "not a url",
        "github.com/pytorch/pytorch",
        "ftp://example.com/x",
        "javascript:alert(1)",
        "https://",
        "https://example",
    ])
    def test_invalid(self, bad):
        assert ExplorationService.valid_url(bad) is False


class TestRecommend:
    def test_normal_recommend_urls_are_verified(self, svc):
        out = svc.recommend(PYTORCH_CTX, limit=3)
        assert len(out) >= 1
        verified = {r["url"] for r in svc.resources}
        for item in out:
            assert item["url"] in verified
            assert item["type"] in ALLOWED_RESOURCE_TYPES
            assert item["reason"]

    def test_filter_by_topic(self, svc):
        out = svc.recommend({"topics": ["Docker"], "weak_points": []}, limit=3)
        assert out
        assert all("docker" in item["resource_id"] for item in out)

    def test_filter_by_knowledge_point(self, svc):
        out = svc.recommend(
            {"knowledge_points": ["RAG"], "weak_points": []}, limit=3
        )
        assert out
        assert all(
            any("rag" in t.lower() for t in r.get("tags", []))
            for r in [next(x for x in svc.resources if x["id"] == o["resource_id"])
                      for o in out]
        )

    def test_weak_point_priority(self, svc):
        out = svc.recommend(
            {
                "topics": ["PyTorch"],
                "knowledge_points": ["pytorch.autograd"],
                "weak_points": ["autograd"],
            },
            limit=2,
        )
        # 带 autograd 标签的资源被薄弱点加权，应排在最前
        assert out[0]["resource_id"] in {"pytorch-gh", "pytorch-docs"}

    def test_no_match_returns_empty(self, svc):
        out = svc.recommend(
            {"topics": ["量子物理"], "knowledge_points": [], "weak_points": []}
        )
        assert out == []

    def test_reachability_excludes_unreachable(self, tmp_path):
        target_url = "https://github.com/pytorch/pytorch"
        checker = lambda url: url != target_url  # noqa: E731
        e = ExplorationService(resources_path=RESOURCE_FILE,
                               resource_checker=checker)
        out = e.recommend({"topics": ["PyTorch"]}, limit=10)
        assert target_url not in {item["url"] for item in out}
        assert all(item["url"] != target_url for item in out)


class TestRecommendWithAI:
    def test_ai_cannot_fabricate_unverified_url(self, svc):
        fake = FakeAI(content=json.dumps({
            "selections": [
                {"resource_id": "does-not-exist",
                 "reason": "可看 https://evil.example/fake 这个假链接"}
            ]
        }))
        out = svc.recommend_with_ai(PYTORCH_CTX, ai_client=fake, limit=3)
        assert out
        verified = {r["url"] for r in svc.resources}
        for item in out:
            assert item["url"] in verified
            assert "https://evil.example/fake" != item["url"]

    def test_ai_normal_selection(self, svc):
        fake = FakeAI(content=json.dumps({
            "selections": [
                {"resource_id": "pytorch-docs",
                 "reason": "官方文档便于查证 Tensor/autograd API"}
            ]
        }))
        out = svc.recommend_with_ai(
            {"topics": ["PyTorch"], "weak_points": []}, ai_client=fake, limit=3
        )
        assert out[0]["resource_id"] == "pytorch-docs"
        assert out[0]["ai_explained"] is True
        verified_url = next(
            r["url"] for r in svc.resources if r["id"] == "pytorch-docs"
        )
        assert out[0]["url"] == verified_url

    def test_ai_failure_falls_back_to_rule(self, svc):
        fake = FakeAI(error=AIServiceError("AI 挂了"))
        out = svc.recommend_with_ai(PYTORCH_CTX, ai_client=fake, limit=3)
        assert out
        verified = {r["url"] for r in svc.resources}
        assert all(item["url"] in verified for item in out)

    def test_ai_not_configured_uses_rules(self, svc):
        fake = FakeAI(configured=False)
        out = svc.recommend_with_ai(PYTORCH_CTX, ai_client=fake, limit=3)
        assert out
        assert all("ai_explained" not in item for item in out)


class TestContextAndIsolation:
    def test_build_context(self, conn, assessment_repo):
        from app.database.study_plan_repository import StudyPlanRepository
        from app.services.study_plan_service import StudyPlanService

        repo = TaskRepository(conn)
        sps = StudyPlanService(repo, StudyPlanRepository(conn))
        sps.ensure_default_plan()
        kp = assessment_repo.create_knowledge_point("pytorch.autograd")
        assessment_repo.update_knowledge_point(
            kp["id"], last_assessed_at="2026-09-05T10:00:00",
            mastery_estimate=0.2, review_count=1,
        )

        ctx = ExplorationService.build_context(
            "2026-09-06", study_plan_service=sps, assessment_repo=assessment_repo
        )
        assert ctx["phase"] == ["阶段二：深度学习与 LLM 基础"]
        assert "PyTorch" in " ".join(ctx["topics"])
        assert "pytorch.autograd" in ctx["knowledge_points"]
        assert "pytorch.autograd" in ctx["weak_points"]  # 掌握度 < 0.5

    def test_recommend_does_not_modify_tasks_or_plan(
        self, conn, assessment_repo
    ):
        from app.database.study_plan_repository import StudyPlanRepository
        from app.services.study_plan_service import StudyPlanService

        repo = TaskRepository(conn)
        sps = StudyPlanService(repo, StudyPlanRepository(conn))
        sps.ensure_default_plan()
        svc = ExplorationService()

        tasks_before = repo.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        topics_before = repo.conn.execute(
            "SELECT COUNT(*) FROM study_topics"
        ).fetchone()[0]
        phase_before = sps.get_current_phase("2026-09-06").name

        ctx = ExplorationService.build_context(
            "2026-09-06", study_plan_service=sps, assessment_repo=assessment_repo
        )
        svc.recommend(ctx)
        svc.recommend_with_ai(ctx, ai_client=FakeAI(
            content=json.dumps({"selections": [
                {"resource_id": "pytorch-docs", "reason": "官方文档查证"}]})
        ))

        assert repo.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == tasks_before
        assert repo.conn.execute(
            "SELECT COUNT(*) FROM study_topics"
        ).fetchone()[0] == topics_before
        assert sps.get_current_phase("2026-09-06").name == phase_before
