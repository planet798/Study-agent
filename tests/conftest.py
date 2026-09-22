"""pytest 共享 fixtures。"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# 必须在任何 Qt import 之前设置，保证无显示环境下也能创建 QApplication
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

# 确保 `app` 包可导入（从项目根目录运行 pytest 时也生效）
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database.connection import get_fresh_connection  # noqa: E402
from app.database.repository import TaskRepository  # noqa: E402
from app.services.task_service import TaskService  # noqa: E402
from app.services.date_service import DateService  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    """每个测试使用独立的临时数据库文件。

    使用 fast fresh path：直接建当前 schema，不重放 v2..v20 历史迁移。
    与 ``get_connection`` 在空库上的最终状态一致（含 learning_routes 种子）。
    迁移 / WAL / release 验证类测试请显式使用 ``get_connection``。
    """
    c = get_fresh_connection(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture()
def repo(conn):
    return TaskRepository(conn)


@pytest.fixture()
def plan_repo(conn):
    """学习计划数据访问层（复用同一 connection）。"""
    from app.database.study_plan_repository import StudyPlanRepository

    return StudyPlanRepository(conn)


@pytest.fixture()
def task_service(repo):
    return TaskService(repo)


@pytest.fixture()
def date_service(repo):
    return DateService(repo)


@pytest.fixture()
def fixed_today():
    """GUI 测试统一使用的固定"今天"，避免依赖真实系统日期。"""
    return "2026-01-05"


@pytest.fixture()
def make_window(qtbot, repo, task_service, date_service, fixed_today):
    """构造一个绑定固定日期的 MainWindow 的工厂。

    用法：
        window = make_window()
        window2 = make_window()  # 每次独立
    """
    def _make():
        window = MainWindow(
            task_service=task_service,
            date_service=date_service,
            today_provider=lambda: fixed_today,
        )
        return window

    return _make


# ============================================================
# AI 设置中心测试 fixtures（fake keyring，绝不接触真实系统凭据）
# ============================================================


class FakeKeyring:
    """内存版 keyring，用于测试；不写真实系统凭据存储。"""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}
        self.fail_on_set = False

    def set_password(self, service, account, value):
        if self.fail_on_set:
            raise RuntimeError("fake keyring unavailable")
        self.store[(service, account)] = value

    def get_password(self, service, account):
        return self.store.get((service, account))

    def delete_password(self, service, account):
        if (service, account) in self.store:
            del self.store[(service, account)]


@pytest.fixture()
def fake_keyring():
    return FakeKeyring()


@pytest.fixture()
def ai_config_service(conn, fake_keyring):
    """绑定临时 DB + fake keyring 的 AIConfigService（env 清空）。"""
    from app.ai.config_service import AIConfigService
    from app.ai.secrets import SecretStore

    return AIConfigService(
        conn=conn,
        secret_store=SecretStore(keyring_module=fake_keyring),
        env={},
    )


@pytest.fixture()
def prompt_registry(conn):
    """DB 支持的 PromptRegistry（支持 override）。"""
    from app.ai.prompt_registry import PromptOverrideRepository, PromptRegistry

    return PromptRegistry(PromptOverrideRepository(conn))


# ============================================================
# Phase 1：六技术路线 fixtures
# ============================================================


@pytest.fixture()
def six_route_env(conn):
    """建好旧“搜广推 + LLM”默认 plan 的测试环境（含 repo 集合）。

    返回 SimpleNamespace：conn / repo / plan_repo / route_repo / skill_repo /
    legacy_route。
    """
    from types import SimpleNamespace

    from app.database.learning_route_repository import LearningRouteRepository
    from app.database.repository import TaskRepository
    from app.database.skill_repository import SkillRepository
    from app.database.study_plan_repository import StudyPlanRepository
    from app.services.study_plan_service import StudyPlanService

    repo = TaskRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    StudyPlanService(repo, plan_repo).ensure_default_plan()
    route_repo = LearningRouteRepository(conn)
    skill_repo = SkillRepository(conn)
    return SimpleNamespace(
        conn=conn,
        repo=repo,
        plan_repo=plan_repo,
        route_repo=route_repo,
        skill_repo=skill_repo,
        legacy_route=route_repo.get_default_learning_route(),
    )


# ============================================================
# Phase 2：Topic Learning Activity fixtures
# ============================================================


@pytest.fixture()
def topic_learning(conn):
    from app.database.topic_learning_repository import (
        TopicLearningComponentRepository,
    )
    from app.services.topic_learning_profile_service import (
        TopicLearningProfileService,
    )

    return TopicLearningProfileService(
        conn, TopicLearningComponentRepository(conn)
    )


@pytest.fixture()
def activity_env(six_route_env, topic_learning):
    """Canonical 六路线 + Topic Learning profiles 的环境。

    返回 SimpleNamespace：conn/repo/plan_repo/route_repo/skill_repo/tl/
    route_id（dict）/ result。
    """
    from types import SimpleNamespace

    from app.services.canonical_route_service import CanonicalRouteService

    result = CanonicalRouteService(
        six_route_env.conn,
        six_route_env.route_repo,
        six_route_env.plan_repo,
        six_route_env.skill_repo,
        topic_learning_service=topic_learning,
    ).ensure_all()
    return SimpleNamespace(
        conn=six_route_env.conn,
        repo=six_route_env.repo,
        plan_repo=six_route_env.plan_repo,
        route_repo=six_route_env.route_repo,
        skill_repo=six_route_env.skill_repo,
        legacy_route=six_route_env.legacy_route,
        tl=topic_learning,
        route_ids=result["route_ids"],
        result=result,
    )


# ============================================================
# Phase 3：Capability fixtures
# ============================================================


@pytest.fixture()
def capability_service(conn):
    from app.database.capability_repository import CapabilityEvidenceRepository
    from app.services.capability_service import CapabilityService

    return CapabilityService(conn, CapabilityEvidenceRepository(conn))


@pytest.fixture()
def assessment_repo(conn):
    from app.database.assessment_repository import AssessmentRepository

    return AssessmentRepository(conn)


# ============================================================
# Phase 4：Practice / Project fixtures
# ============================================================


@pytest.fixture()
def practice_env(conn):
    """Canonical 六路线 + PracticeProjectService 环境。"""
    from types import SimpleNamespace

    from app.database.learning_route_repository import LearningRouteRepository
    from app.database.practice_repository import (
        PracticeMilestoneRepository,
        PracticeOutputRepository,
        PracticeProjectRepository,
    )
    from app.database.repository import TaskRepository
    from app.database.skill_repository import SkillRepository
    from app.database.study_plan_repository import StudyPlanRepository
    from app.services.canonical_route_service import CanonicalRouteService
    from app.services.practice_project_service import PracticeProjectService
    from app.services.study_plan_service import StudyPlanService

    repo = TaskRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    StudyPlanService(repo, plan_repo).ensure_default_plan()
    route_repo = LearningRouteRepository(conn)
    skill_repo = SkillRepository(conn)
    CanonicalRouteService(conn, route_repo, plan_repo, skill_repo).ensure_all()
    service = PracticeProjectService(
        conn,
        project_repo=PracticeProjectRepository(conn),
        milestone_repo=PracticeMilestoneRepository(conn),
        output_repo=PracticeOutputRepository(conn),
        route_repo=route_repo,
        plan_repo=plan_repo,
        skill_repo=skill_repo,
    )
    return SimpleNamespace(
        conn=conn, repo=repo, plan_repo=plan_repo, route_repo=route_repo,
        skill_repo=skill_repo, service=service,
        r1=route_repo.get_by_key("R1_LLM_FUNDAMENTALS"),
        r2=route_repo.get_by_key("R2_LLM_POST_TRAINING"),
        r3=route_repo.get_by_key("R3_LLM_INFRA"),
        r4=route_repo.get_by_key("R4_AI_AGENT"),
        r5=route_repo.get_by_key("R5_RECOMMENDATION_SEARCH"),
        r6=route_repo.get_by_key("R6_CS_FUNDAMENTALS"),
        group=route_repo.get_by_key("JOB_PREP"),
    )


# ============================================================
# Phase 5：Practice → Capability fixtures
# ============================================================


@pytest.fixture()
def practice_capability_env(practice_env):
    """在 practice_env 基础上构建 PracticeCapabilityService。"""
    from types import SimpleNamespace

    from app.database.capability_repository import CapabilityEvidenceRepository
    from app.database.practice_repository import (
        PracticeOutputRepository,
        PracticeProjectRepository,
        PracticeTopicEvidenceRepository,
    )
    from app.services.capability_service import CapabilityService
    from app.services.practice_capability_service import (
        PracticeCapabilityService,
    )

    conn = practice_env.conn
    evidence_repo = PracticeTopicEvidenceRepository(conn)
    cap = CapabilityService(
        conn, CapabilityEvidenceRepository(conn),
        practice_evidence_repo=evidence_repo,
        practice_project_repo=PracticeProjectRepository(conn),
    )
    pc = PracticeCapabilityService(
        conn,
        evidence_repo=evidence_repo,
        service=cap,
        project_repo=PracticeProjectRepository(conn),
        output_repo=PracticeOutputRepository(conn),
        plan_repo=practice_env.plan_repo,
        route_repo=practice_env.route_repo,
    )
    return SimpleNamespace(
        conn=conn, repo=practice_env.repo, plan_repo=practice_env.plan_repo,
        route_repo=practice_env.route_repo, skill_repo=practice_env.skill_repo,
        service=practice_env.service, pc=pc, cap=cap,
        evidence_repo=evidence_repo,
        r1=practice_env.r1, r2=practice_env.r2, r3=practice_env.r3,
        r4=practice_env.r4, r5=practice_env.r5, r6=practice_env.r6,
        group=practice_env.group,
    )


# ============================================================
# Phase 6：Practice Planner Feedback fixtures
# ============================================================


@pytest.fixture()
def practice_readiness_env(practice_env):
    """Canonical 六路线 + Requirement / Readiness / Feedback。

    readiness 的 current phase / prerequisite gate 绑定到 route-scoped
    StudyPlanService（与生产 Scheduler 一致）。
    """
    from types import SimpleNamespace

    from app.database.assessment_repository import AssessmentRepository
    from app.database.capability_repository import CapabilityEvidenceRepository
    from app.database.practice_repository import PracticeRequirementRepository
    from app.database.topic_learning_repository import (
        TopicLearningComponentRepository,
    )
    from app.services.capability_service import CapabilityService
    from app.services.planner_feedback import PlannerFeedbackService
    from app.services.practice_readiness import PracticeReadinessService
    from app.services.study_plan_service import StudyPlanService
    from app.services.topic_learning_profile_service import (
        TopicLearningProfileService,
    )

    conn = practice_env.conn
    repo = practice_env.repo
    plan_repo = practice_env.plan_repo
    route_repo = practice_env.route_repo
    arepo = AssessmentRepository(conn)
    tl = TopicLearningProfileService(
        conn, TopicLearningComponentRepository(conn)
    )
    cap = CapabilityService(conn, CapabilityEvidenceRepository(conn))
    requirement_repo = PracticeRequirementRepository(conn)

    cache: dict[int, object] = {}

    def sps_for(route_id: int):
        if route_id not in cache:
            cache[route_id] = StudyPlanService(
                repo, plan_repo, route_id=route_id,
                learning_route_repo=route_repo, scope_tasks_by_route=True,
                topic_learning_service=tl, assessment_repo=arepo,
            )
        return cache[route_id]

    def _blocked(route_id: int, plan_date: str):
        try:
            service = sps_for(route_id)
            phase = service.get_current_phase(plan_date)
            if phase is None:
                return set()
            blocked, _ = service.skill_topic_views(list(phase.topics), plan_date)
            return {int(x) for x in blocked}
        except Exception:  # noqa: BLE001
            return set()

    readiness = PracticeReadinessService(
        conn,
        requirement_repo=requirement_repo,
        plan_repo=plan_repo,
        route_repo=route_repo,
        capability_repo=cap.repo,
        topic_learning_service=tl,
        task_repo=repo,
        current_phase_provider=lambda rid, d: sps_for(rid).get_current_phase(d),
        blocked_topic_provider=_blocked,
    )
    feedback = PlannerFeedbackService(
        conn, readiness_service=readiness, plan_repo=plan_repo,
        route_repo=route_repo, skill_service=None, task_repo=repo,
    )
    return SimpleNamespace(
        conn=conn, repo=repo, plan_repo=plan_repo, route_repo=route_repo,
        skill_repo=practice_env.skill_repo, service=practice_env.service,
        cap=cap, requirement_repo=requirement_repo,
        readiness=readiness, feedback=feedback, tl=tl, arepo=arepo,
        sps_for=sps_for,
        r1=practice_env.r1, r2=practice_env.r2, r3=practice_env.r3,
        r4=practice_env.r4, r5=practice_env.r5, r6=practice_env.r6,
        group=practice_env.group,
    )


# ============================================================
# Category markers（只加标签，默认仍跑完整 suite）
# ============================================================
# 开发时可以：
#   pytest -m "not slow"            # 快速迭代（跳过迁移/集成类）
#   pytest -m migration             # 只跑迁移相关
#   pytest -m ui                    # 只跑 GUI/offscreen
#   pytest -m integration           # 只跑端到端/调度集成
# 默认 ``pytest -q`` 语义完全不变。

_MIGRATION_RE = re.compile(r"migration|verifier|legacy|schema_migrations")
_SLOW_RE = re.compile(
    r"end_to_end|phase6_regression|multi_route_scheduler|stabilization_legacy"
    r"|release_migration|migration_verifier"
)


def pytest_collection_modifyitems(config, items):
    for item in items:
        nodeid = item.nodeid.lower()
        if _MIGRATION_RE.search(nodeid):
            item.add_marker(pytest.mark.migration)
        if "qtbot" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.ui)
        if _SLOW_RE.search(nodeid):
            item.add_marker(pytest.mark.integration)
            item.add_marker(pytest.mark.slow)
        elif _MIGRATION_RE.search(nodeid):
            item.add_marker(pytest.mark.slow)
