"""PracticeProjectService（Phase 4 / Phase 5.1）。

职责：
- PracticeProject CRUD / status / archive / restore / delete 保护；
- Project ↔ Route / Skill / Topic 的 N:N 关系（含一致性校验与事务）；
- Milestone / Output 的 CRUD。

严格边界：
- Practice 不是 LearningRoute，也不影响 Planner / Scheduler / Review /
  Mastery / Capability；
- 不自动创建项目，不自动生成 LearningOutcome / capability evidence。

Phase 5 追加保护：
- Output 被 active PracticeTopicEvidence 引用时：禁止删除、锁定关键字段修改；
- Topic 存在 active evidence 时禁止解除关联；
- 项目只要产生过 PracticeTopicEvidence（含已撤销历史）就禁止物理删除。

Phase 5.1（Historical Integrity）：
- 只要 Output 曾被**任意** evidence（含已撤销）引用，就禁止物理删除；
  且 `output_type / description / uri / details` 永久冻结（仅允许改 `title`）；
- 只要 Project/Topic 存在过任意 evidence（含 revoked），就禁止移除该 Topic 关联，
  从而对应 Route 关联也无法被拆掉；
- revoke 仅表示“不再参与 current capability”，不代表历史不存在。
"""

from __future__ import annotations

import re
import sqlite3
from typing import Optional

from ..database.learning_route_repository import LearningRouteRepository
from ..database.practice_repository import (
    PracticeMilestoneRepository,
    PracticeOutputRepository,
    PracticeProjectRepository,
    PracticeTopicEvidenceRepository,
)
from ..database.study_plan_repository import StudyPlanRepository
from ..utils.date_utils import now_iso
from .practice import (
    ALL_MILESTONE_STATUSES,
    ALL_PROJECT_TYPES,
    OUTPUT_TYPES,
    SOURCE_MANUAL,
    STATUS_ARCHIVED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_PLANNED,
    is_valid_project_status,
)

# 秘密信息检测（沿用 AI Settings 的 secret-safety 原则）
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|token|password|passwd|secret)\s*[:=]\s*\S+"),
    re.compile(r"://[^/\s:@]+:[^/\s@]+@"),  # https://user:pass@host
)


def _assert_no_secret(*values: str | None) -> None:
    for value in values:
        if not value:
            continue
        for pat in _SECRET_PATTERNS:
            if pat.search(str(value)):
                raise ValueError(
                    "检测到疑似密钥/凭据内容，Practice Output 不允许保存秘密信息。"
                )


class PracticeError(ValueError):
    """Practice 业务校验失败。"""


class PracticeProjectService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        project_repo: PracticeProjectRepository | None = None,
        milestone_repo: PracticeMilestoneRepository | None = None,
        output_repo: PracticeOutputRepository | None = None,
        route_repo: LearningRouteRepository | None = None,
        plan_repo: StudyPlanRepository | None = None,
        skill_repo=None,
        evidence_repo: PracticeTopicEvidenceRepository | None = None,
    ):
        self.conn = conn
        self.projects = project_repo or PracticeProjectRepository(conn)
        self.milestones = milestone_repo or PracticeMilestoneRepository(conn)
        self.outputs = output_repo or PracticeOutputRepository(conn)
        self.route_repo = route_repo or LearningRouteRepository(conn)
        self.plan_repo = plan_repo or StudyPlanRepository(conn)
        self.skill_repo = skill_repo
        self.evidence_repo = evidence_repo or PracticeTopicEvidenceRepository(conn)

    # ================= Project =================

    def create_project(
        self,
        name: str,
        project_type: str = "other",
        description: str = "",
        goal: str = "",
        status: str = STATUS_PLANNED,
        source: str = SOURCE_MANUAL,
        route_ids: list[int] | None = None,
        started_at: str | None = None,
        target_date: str | None = None,
    ) -> dict:
        """创建项目 + 初始 Route 关系（同一事务）。Phase 4 禁止 AI 自动创建。"""
        if project_type not in ALL_PROJECT_TYPES:
            raise PracticeError(f"非法项目类型: {project_type!r}")
        if not is_valid_project_status(status) or status == STATUS_ARCHIVED:
            raise PracticeError(f"非法初始状态: {status!r}")
        _assert_no_secret(name, description, goal)
        route_ids = self._validate_route_ids(route_ids or [])
        try:
            self.conn.execute("BEGIN")
            project = self.projects.create(
                name=name, project_type=project_type, status=status,
                description=description, goal=goal, source=source,
                started_at=started_at, target_date=target_date,
            )
            for rid in route_ids:
                self.conn.execute(
                    "INSERT OR IGNORE INTO practice_project_routes "
                    "(project_id, route_id, created_at) VALUES (?, ?, ?)",
                    (project["id"], rid, now_iso()),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.projects.get(project["id"])

    def update_project(self, project_id: int, **fields) -> dict:
        _assert_no_secret(fields.get("name"), fields.get("description"),
                          fields.get("goal"))
        if fields.get("project_type") is not None and \
                fields["project_type"] not in ALL_PROJECT_TYPES:
            raise PracticeError("非法项目类型")
        return self.projects.update(project_id, **fields) or self._require(
            project_id
        )

    def set_status(self, project_id: int, status: str) -> dict:
        if not is_valid_project_status(status) or status == STATUS_ARCHIVED:
            raise PracticeError(f"非法状态: {status!r}（归档请用 archive_project）")
        project = self._require(project_id)
        fields: dict = {"status": status}
        if status == STATUS_COMPLETED:
            fields["completed_at"] = now_iso()
        elif project["status"] == STATUS_COMPLETED:
            # completed → in_progress：清空当前 completed_at
            fields["completed_at"] = None
        if status == STATUS_IN_PROGRESS and not project.get("started_at"):
            fields["started_at"] = now_iso()
        return self.projects.update(project_id, **fields)

    def archive_project(self, project_id: int) -> dict:
        project = self._require(project_id)
        if project["status"] == STATUS_ARCHIVED:
            return project
        return self.projects.update(
            project_id,
            archived_from_status=project["status"],
            status=STATUS_ARCHIVED,
        )

    def restore_project(self, project_id: int) -> dict:
        project = self._require(project_id)
        if project["status"] != STATUS_ARCHIVED:
            return project
        restored = project.get("archived_from_status") or STATUS_IN_PROGRESS
        if restored == STATUS_ARCHIVED:
            restored = STATUS_IN_PROGRESS
        return self.projects.update(
            project_id, status=restored, archived_from_status=None
        )

    def delete_project(self, project_id: int) -> bool:
        """仅空壳项目允许物理删除；有 milestone/output/relation 必须归档。

        Phase 5：只要产生过 PracticeTopicEvidence（含已撤销的历史行），
        项目就是历史能力证据来源，禁止物理删除。
        """
        self._require(project_id)
        if self.evidence_repo.has_any_evidence(project_id):
            raise PracticeError(
                "该项目已产生项目能力证据（含已撤销历史），不能物理删除；"
                "请改为归档。"
            )
        if self.projects.has_history(project_id):
            raise PracticeError(
                "该项目已有里程碑 / 成果 / 关联，不能物理删除；请改为归档。"
            )
        return self.projects.delete(project_id)

    def _require(self, project_id: int) -> dict:
        project = self.projects.get(project_id)
        if project is None:
            raise PracticeError(f"项目不存在: id={project_id}")
        return project

    # ================= Route relations =================

    def _validate_route_ids(self, route_ids: list[int]) -> list[int]:
        out: list[int] = []
        for rid in route_ids:
            route = self.route_repo.get(int(rid))
            if route is None:
                raise PracticeError(f"学习路线不存在: id={rid}")
            if route.route_type != "learning":
                raise PracticeError("只能关联 learning 路线，不能关联分组")
            if route.is_archived:
                raise PracticeError(f"已归档路线不能新增关联: {route.name}")
            if int(rid) not in out:
                out.append(int(rid))
        return out

    def add_route(self, project_id: int, route_id: int) -> bool:
        self._require(project_id)
        if not self._validate_route_ids([route_id]):
            return False
        return self.projects.add_route(project_id, route_id)

    def remove_route(self, project_id: int, route_id: int) -> bool:
        self._require(project_id)
        linked_topics = self.projects.list_topic_ids(project_id)
        for tid in linked_topics:
            if self.plan_repo.get_route_id_for_topic(tid) == int(route_id):
                raise PracticeError(
                    "该路线仍有项目关联 Topic，请先解除 Topic 关联。"
                )
        return self.projects.remove_route(project_id, route_id)

    def set_routes(self, project_id: int, route_ids: list[int]) -> dict:
        """替换项目 Route 关系；删除有 Topic 关联的 route 会整体拒绝。"""
        self._require(project_id)
        validated = self._validate_route_ids(route_ids)
        current = set(self.projects.list_route_ids(project_id))
        removed = current - set(validated)
        linked_topics = self.projects.list_topic_ids(project_id)
        for tid in linked_topics:
            if self.plan_repo.get_route_id_for_topic(tid) in removed:
                raise PracticeError(
                    "被移除的路线仍有项目关联 Topic，请先解除 Topic 关联。"
                )
        try:
            self.conn.execute("BEGIN")
            self.conn.execute(
                "DELETE FROM practice_project_routes WHERE project_id = ?",
                (int(project_id),),
            )
            for rid in validated:
                self.conn.execute(
                    "INSERT OR IGNORE INTO practice_project_routes "
                    "(project_id, route_id, created_at) VALUES (?, ?, ?)",
                    (int(project_id), rid, now_iso()),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get_project_detail(project_id)

    # ================= Skill relations =================

    def add_skill(self, project_id: int, skill_id: int) -> bool:
        self._require(project_id)
        if self.skill_repo is not None and self.skill_repo.get(skill_id) is None:
            raise PracticeError(f"技能不存在: id={skill_id}")
        # 注意：添加 Project Skill 绝不自动创建 route_skills
        return self.projects.add_skill(project_id, skill_id)

    def remove_skill(self, project_id: int, skill_id: int) -> bool:
        self._require(project_id)
        return self.projects.remove_skill(project_id, skill_id)

    def set_skills(self, project_id: int, skill_ids: list[int]) -> dict:
        self._require(project_id)
        for sid in skill_ids:
            if self.skill_repo is not None and self.skill_repo.get(sid) is None:
                raise PracticeError(f"技能不存在: id={sid}")
        try:
            self.conn.execute("BEGIN")
            self.conn.execute(
                "DELETE FROM practice_project_skills WHERE project_id = ?",
                (int(project_id),),
            )
            for sid in dict.fromkeys(int(s) for s in skill_ids):
                self.conn.execute(
                    "INSERT OR IGNORE INTO practice_project_skills "
                    "(project_id, skill_id, created_at) VALUES (?, ?, ?)",
                    (int(project_id), sid, now_iso()),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get_project_detail(project_id)

    # ================= Topic relations =================

    def _assert_topic_in_project_routes(self, project_id: int, topic_id: int) -> None:
        topic_route = self.plan_repo.get_route_id_for_topic(topic_id)
        if topic_route is None:
            raise PracticeError(f"Topic 不存在或未绑定路线: id={topic_id}")
        if topic_route not in self.projects.list_route_ids(project_id):
            raise PracticeError(
                "Topic 所属路线不在项目关联路线中；请先关联该路线。"
            )

    def add_topic(self, project_id: int, topic_id: int) -> bool:
        self._require(project_id)
        self._assert_topic_in_project_routes(project_id, topic_id)
        return self.projects.add_topic(project_id, topic_id)

    def remove_topic(self, project_id: int, topic_id: int) -> bool:
        self._require(project_id)
        if self.evidence_repo.has_active_topic_reference(project_id, topic_id):
            raise PracticeError(
                "该 Topic 存在生效的项目使用证据，不能解除关联；"
                "请先撤销对应能力证据。"
            )
        if self.evidence_repo.has_any_topic_reference(project_id, topic_id):
            raise PracticeError(
                "该 Topic 曾产生项目能力证据（含已撤销历史），"
                "为保留历史证据链不能解除关联。"
            )
        return self.projects.remove_topic(project_id, topic_id)

    def set_topics(self, project_id: int, topic_ids: list[int]) -> dict:
        self._require(project_id)
        for tid in topic_ids:
            self._assert_topic_in_project_routes(project_id, tid)
        removed = set(self.projects.list_topic_ids(project_id)) - set(
            int(t) for t in topic_ids
        )
        for tid in removed:
            if self.evidence_repo.has_active_topic_reference(project_id, tid):
                raise PracticeError(
                    "被移除的 Topic 存在生效的项目使用证据，请先撤销能力证据。"
                )
            if self.evidence_repo.has_any_topic_reference(project_id, tid):
                raise PracticeError(
                    "被移除的 Topic 曾产生项目能力证据（含已撤销历史），"
                    "为保留历史证据链不能移除。"
                )
        try:
            self.conn.execute("BEGIN")
            self.conn.execute(
                "DELETE FROM practice_project_topics WHERE project_id = ?",
                (int(project_id),),
            )
            for tid in dict.fromkeys(int(t) for t in topic_ids):
                self.conn.execute(
                    "INSERT OR IGNORE INTO practice_project_topics "
                    "(project_id, topic_id, created_at) VALUES (?, ?, ?)",
                    (int(project_id), tid, now_iso()),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get_project_detail(project_id)

    # ================= detail / progress / queries =================

    def get_project_detail(self, project_id: int) -> dict:
        project = self._require(project_id)
        route_ids = self.projects.list_route_ids(project_id)
        skill_ids = self.projects.list_skill_ids(project_id)
        topic_ids = self.projects.list_topic_ids(project_id)
        routes = [self.route_repo.get(r) for r in route_ids]
        routes = [r for r in routes if r is not None]
        skills = []
        if self.skill_repo is not None:
            skills = [self.skill_repo.get(s) for s in skill_ids]
            skills = [s for s in skills if s is not None]
        topics = []
        for tid in topic_ids:
            t = self.plan_repo.get_topic(tid)
            if t is not None:
                topics.append({
                    "id": t.id, "name": t.name,
                    "route_id": self.plan_repo.get_route_id_for_topic(t.id),
                })
        return {
            "project": project,
            "routes": routes,
            "skills": skills,
            "topics": topics,
            "milestones": self.milestones.list_by_project(project_id),
            "outputs": self.outputs.list_by_project(project_id),
        }

    def get_project_progress(self, project_id: int) -> dict:
        done, total = self.milestones.count_by_project(project_id)
        return {
            "milestones_done": done,
            "milestones_total": total,
            "has_milestones": total > 0,
            "output_count": self.outputs.count_by_project(project_id),
        }

    def list_projects(self, status: str | None = None) -> list[dict]:
        return self.projects.list_all(status=status)

    def list_by_route(self, route_id: int) -> list[dict]:
        return self.projects.list_by_route(route_id)

    def list_by_skill(self, skill_id: int) -> list[dict]:
        return self.projects.list_by_skill(skill_id)

    def list_by_topic(self, topic_id: int) -> list[dict]:
        return self.projects.list_by_topic(topic_id)

    def count_by_route(self, route_id: int) -> int:
        return self.projects.count_by_route(route_id)

    def count_by_skill(self, skill_id: int) -> int:
        return self.projects.count_by_skill(skill_id)

    def count_by_topic(self, topic_id: int) -> int:
        return self.projects.count_by_topic(topic_id)

    # ================= Milestones =================

    def add_milestone(self, project_id: int, title: str,
                      description: str = "", status: str = "todo",
                      order_index: int | None = None) -> dict:
        self._require(project_id)
        if status not in ALL_MILESTONE_STATUSES:
            raise PracticeError(f"非法里程碑状态: {status!r}")
        return self.milestones.create(
            project_id, title, description, status, order_index
        )

    def update_milestone(self, milestone_id: int, **fields) -> dict:
        m = self.milestones.get(milestone_id)
        if m is None:
            raise PracticeError(f"里程碑不存在: id={milestone_id}")
        return self.milestones.update(milestone_id, **fields)

    def set_milestone_status(self, milestone_id: int, status: str) -> dict:
        return self.update_milestone(milestone_id, status=status)

    def reorder_milestones(self, project_id: int, ordered_ids: list[int]) -> None:
        self._require(project_id)
        self.milestones.reorder(project_id, ordered_ids)

    def delete_milestone(self, milestone_id: int) -> bool:
        """已完成过的里程碑不物理删除（保留历史事实）。"""
        m = self.milestones.get(milestone_id)
        if m is None:
            return False
        if m["status"] == "done" or m.get("completed_at"):
            raise PracticeError(
                "已完成过的里程碑不能删除；请将其重新打开或保留。"
            )
        return self.milestones.delete(milestone_id)

    # ================= Outputs =================

    def add_output(self, project_id: int, output_type: str, title: str,
                   description: str = "", uri: str | None = None,
                   details: dict | None = None) -> dict:
        self._require(project_id)
        if output_type not in OUTPUT_TYPES:
            raise PracticeError(f"非法成果类型: {output_type!r}")
        _assert_no_secret(uri, title, description)
        return self.outputs.create(
            project_id, output_type, title, description, uri, details
        )

    _EVIDENCE_LOCKED_OUTPUT_FIELDS = (
        "output_type", "uri", "details", "description",
    )

    def update_output(self, output_id: int, **fields) -> dict:
        o = self.outputs.get(output_id)
        if o is None:
            raise PracticeError(f"成果不存在: id={output_id}")
        if fields.get("output_type") is not None and \
                fields["output_type"] not in OUTPUT_TYPES:
            raise PracticeError("非法成果类型")
        _assert_no_secret(fields.get("uri"), fields.get("title"),
                          fields.get("description"))
        if self.evidence_repo.has_any_output_reference(output_id):
            locked = [
                k for k in self._EVIDENCE_LOCKED_OUTPUT_FIELDS
                if k in fields
            ]
            if locked:
                raise PracticeError(
                    "该成果曾作为项目能力证据使用，类型 / 链接 / 结构化信息 / "
                    "说明已永久冻结；为保留历史证据链不能修改（仅可改标题）。"
                )
        return self.outputs.update(output_id, **fields)

    def delete_output(self, output_id: int) -> bool:
        """历史完整性：曾被任意 PracticeTopicEvidence 引用（含已撤销）→ 禁止删除。"""
        if self.evidence_repo.has_any_output_reference(output_id):
            raise PracticeError(
                "该成果曾作为项目能力证据使用，为保留历史证据链不能删除。"
            )
        return self.outputs.delete(output_id)

    def output_count(self, project_id: int) -> int:
        return self.outputs.count_by_project(project_id)
