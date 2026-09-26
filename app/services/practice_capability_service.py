"""PracticeCapabilityService（Phase 5）。

职责：**只有**这个 Service 能产生 Level 5 PROJECT 能力证据。

证据链：

    PracticeProject
      ↓
    Project Topic (practice_project_topics)
      ↓
    PracticeTopicEvidence（用户确认的原始事实）
      ↓
    selected PracticeOutputs
      ↓
    CapabilityEvidence(PROJECT)

严格边界（禁止）：
- 不自动遍历 Project Topics；不自动 backfill；
- 不因 project completed / outputs / topic relation 自动产生 PROJECT；
- 不影响 Planner / Scheduler / Review / Mastery / Activity / Curriculum；
- 不做 AI 自动判断；
- 不访问网络“外部验证”。
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from ..database.learning_route_repository import LearningRouteRepository
from ..database.practice_repository import (
    PracticeOutputRepository,
    PracticeProjectRepository,
    PracticeTopicEvidenceRepository,
)
from ..database.study_plan_repository import StudyPlanRepository
from .capability_service import CapabilityService
from .practice import (
    STATUS_ARCHIVED,
    STATUS_COMPLETED,
    output_type_label,
)
from .practice_evidence import (
    REASON_ALREADY_ACTIVE,
    REASON_CONFIRMATION_REQUIRED,
    REASON_EVIDENCE_NOT_FOUND,
    REASON_KP_CONFLICT,
    REASON_KP_ROUTE_MISMATCH,
    REASON_NO_OUTPUTS,
    REASON_NO_QUALIFYING_OUTPUT,
    REASON_OK,
    REASON_OUTPUT_NOT_IN_PROJECT,
    REASON_PROJECT_ARCHIVED,
    REASON_PROJECT_NOT_COMPLETED,
    REASON_PROJECT_NOT_FOUND,
    REASON_ROUTE_MISMATCH,
    REASON_TOPIC_NOT_FOUND,
    REASON_TOPIC_RELATION_MISSING,
    REASON_USAGE_DESCRIPTION_REQUIRED,
    is_qualifying_output,
    output_evidence_role,
    reason_label,
)


class PracticeCapabilityError(ValueError):
    """Practice → Capability 校验失败（带 reason code）。"""

    def __init__(self, message: str, reason: str = ""):
        super().__init__(message)
        self.reason = reason or ""


def evidence_key_for(evidence_id: int) -> str:
    return f"practice_topic_evidence:{int(evidence_id)}"


class PracticeCapabilityService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        evidence_repo: PracticeTopicEvidenceRepository | None = None,
        service: CapabilityService | None = None,
        project_repo: PracticeProjectRepository | None = None,
        output_repo: PracticeOutputRepository | None = None,
        plan_repo: StudyPlanRepository | None = None,
        route_repo: LearningRouteRepository | None = None,
    ):
        self.conn = conn
        self.evidence_repo = evidence_repo or PracticeTopicEvidenceRepository(conn)
        self.capability_service = service or CapabilityService(conn)
        self.project_repo = project_repo or PracticeProjectRepository(conn)
        self.output_repo = output_repo or PracticeOutputRepository(conn)
        self.plan_repo = plan_repo or StudyPlanRepository(conn)
        self.route_repo = route_repo or LearningRouteRepository(conn)

    # ================= 只读查询 =================

    def get_topic_evidence(self, evidence_id: int) -> Optional[dict]:
        evidence = self.evidence_repo.get(evidence_id)
        if evidence is None:
            return None
        return self._enrich(evidence)

    def list_project_evidence(
        self, project_id: int, active_only: bool = False
    ) -> list[dict]:
        return [
            self._enrich(e)
            for e in self.evidence_repo.list_by_project(
                project_id, active_only=active_only
            )
        ]

    def list_by_topic(
        self, topic_id: int, active_only: bool = False
    ) -> list[dict]:
        return [
            self._enrich(e)
            for e in self.evidence_repo.list_by_topic(
                topic_id, active_only=active_only
            )
        ]

    def list_by_kp(
        self, knowledge_point_id: int, active_only: bool = False
    ) -> list[dict]:
        return [
            self._enrich(e)
            for e in self.evidence_repo.list_by_kp(
                knowledge_point_id, active_only=active_only
            )
        ]

    def count_active_by_project(self, project_id: int) -> int:
        return self.evidence_repo.count_active_by_project(project_id)

    def count_active_by_topic(self, topic_id: int) -> int:
        return self.evidence_repo.count_active_by_topic(topic_id)

    def has_active_output_reference(self, output_id: int) -> bool:
        return self.evidence_repo.has_active_output_reference(output_id)

    def has_any_output_reference(self, output_id: int) -> bool:
        """曾被任意 evidence（含已撤销）引用 —— 历史完整性保护。"""
        return self.evidence_repo.has_any_output_reference(output_id)

    def count_created_between(self, start: str, end: str) -> int:
        return self.evidence_repo.count_created_between(start, end)

    def get_active_for_project_topic(
        self, project_id: int, topic_id: int
    ) -> Optional[dict]:
        evidence = self.evidence_repo.get_active_by_project_topic(
            project_id, topic_id
        )
        return self._enrich(evidence) if evidence else None

    def _enrich(self, evidence: dict) -> dict:
        """补充只读展示信息（project / topic / route / outputs）。"""
        out = dict(evidence)
        project = self.project_repo.get(evidence["project_id"])
        topic = self.plan_repo.get_topic(evidence["topic_id"])
        route_id = self.plan_repo.get_route_id_for_topic(evidence["topic_id"])
        route_name = ""
        if route_id is not None:
            route = self.route_repo.get(int(route_id))
            if route is not None:
                route_name = getattr(route, "name", "") or ""
        outputs = self.evidence_repo.list_outputs(evidence["id"])
        out["project_name"] = (project or {}).get("name", "")
        out["project_status"] = (project or {}).get("status", "")
        out["topic_name"] = getattr(topic, "name", "") if topic else ""
        out["route_id"] = route_id
        out["route_name"] = route_name
        out["outputs"] = outputs
        out["output_types"] = [
            o.get("output_type") for o in outputs
        ]
        out["output_labels"] = [
            output_type_label(o.get("output_type")) for o in outputs
        ]
        out["capability_evidence_key"] = evidence_key_for(evidence["id"])
        return out

    # ================= 候选 / 可形成证据预览 =================

    def list_evidence_candidates(self, project_id: int) -> list[dict]:
        """Project Detail 的“可形成证据”列表（只读，不写 capability）。"""
        project = self.project_repo.get(project_id)
        if project is None:
            raise PracticeCapabilityError("项目不存在", REASON_PROJECT_NOT_FOUND)
        route_ids = set(self.project_repo.list_route_ids(project_id))
        outputs = self.output_repo.list_by_project(project_id)
        qualifying = [o for o in outputs if is_qualifying_output(o)]
        out: list[dict] = []
        for topic_id in self.project_repo.list_topic_ids(project_id):
            topic = self.plan_repo.get_topic(topic_id)
            route_id = self.plan_repo.get_route_id_for_topic(topic_id)
            active = self.evidence_repo.get_active_by_project_topic(
                project_id, topic_id
            )
            resolution = self._resolve_knowledge_point(topic.id, topic.name, route_id)
            eligible, reason = self._candidate_reason(
                project, topic, route_id, route_ids, outputs, qualifying, active
            )
            kp_id = resolution.get("kp_id")
            if kp_id is None and active is not None:
                kp_id = active.get("knowledge_point_id")
            out.append({
                "topic_id": int(topic_id),
                "topic_name": getattr(topic, "name", "") if topic else "",
                "route_id": route_id,
                "knowledge_point_id": kp_id,
                "eligible": eligible,
                "reason": reason,
                "reason_label": reason_label(reason),
                "has_active_evidence": active is not None,
                "active_evidence_id": active["id"] if active else None,
                "has_historical_evidence": (
                    self.evidence_repo.has_any_topic_reference(
                        project_id, topic_id
                    )
                ),
                "qualifying_output_count": len(qualifying),
                "output_count": len(outputs),
            })
        return out

    def _candidate_reason(
        self, project, topic, route_id, route_ids, outputs, qualifying, active
    ) -> tuple[bool, str]:
        if project["status"] == STATUS_ARCHIVED:
            return False, REASON_PROJECT_ARCHIVED
        if project["status"] != STATUS_COMPLETED:
            return False, REASON_PROJECT_NOT_COMPLETED
        if active is not None:
            return False, REASON_ALREADY_ACTIVE
        if topic is None:
            return False, REASON_TOPIC_NOT_FOUND
        if route_id is None or route_id not in route_ids:
            return False, REASON_ROUTE_MISMATCH
        resolution = self._resolve_knowledge_point(topic.id, topic.name, route_id)
        if resolution["status"] == "conflict":
            return False, resolution["reason"]
        if not outputs:
            return False, REASON_NO_OUTPUTS
        if not qualifying:
            return False, REASON_NO_QUALIFYING_OUTPUT
        return True, REASON_OK

    # ================= 校验 =================

    def validate_project_topic_evidence(
        self,
        project_id: int,
        topic_id: int,
        output_ids: list[int],
        usage_description: str,
        confirmed: bool = False,
    ) -> dict:
        """只读校验；返回 ok/reason 与解析出的 kp（不写库）。"""
        result = {
            "ok": False,
            "reason": REASON_OK,
            "reason_label": "",
            "knowledge_point_id": None,
            "kp_resolution": "",
            "project_id": int(project_id),
            "topic_id": int(topic_id),
            "outputs": [],
            "qualifying_outputs": [],
        }

        def _fail(reason: str) -> dict:
            result["reason"] = reason
            result["reason_label"] = reason_label(reason)
            return result

        project = self.project_repo.get(project_id)
        if project is None:
            return _fail(REASON_PROJECT_NOT_FOUND)
        if project["status"] == STATUS_ARCHIVED:
            return _fail(REASON_PROJECT_ARCHIVED)
        if project["status"] != STATUS_COMPLETED:
            return _fail(REASON_PROJECT_NOT_COMPLETED)

        if topic_id not in self.project_repo.list_topic_ids(project_id):
            return _fail(REASON_TOPIC_RELATION_MISSING)
        topic = self.plan_repo.get_topic(topic_id)
        if topic is None:
            return _fail(REASON_TOPIC_NOT_FOUND)

        route_id = self.plan_repo.get_route_id_for_topic(topic_id)
        route_ids = set(self.project_repo.list_route_ids(project_id))
        if route_id is None or route_id not in route_ids:
            return _fail(REASON_ROUTE_MISMATCH)

        if self.evidence_repo.get_active_by_project_topic(project_id, topic_id):
            return _fail(REASON_ALREADY_ACTIVE)

        resolution = self._resolve_knowledge_point(topic.id, topic.name, route_id)
        if resolution["status"] == "conflict":
            return _fail(resolution["reason"])
        result["knowledge_point_id"] = resolution["kp_id"]
        result["kp_resolution"] = resolution["status"]

        unique_ids = list(dict.fromkeys(int(o) for o in (output_ids or [])))
        if not unique_ids:
            return _fail(REASON_NO_OUTPUTS)
        project_outputs = {
            int(o["id"]): o for o in self.output_repo.list_by_project(project_id)
        }
        selected = []
        for oid in unique_ids:
            if oid not in project_outputs:
                return _fail(REASON_OUTPUT_NOT_IN_PROJECT)
            selected.append(project_outputs[oid])
        result["outputs"] = selected
        qualifying = [o for o in selected if is_qualifying_output(o)]
        result["qualifying_outputs"] = qualifying
        if not qualifying:
            return _fail(REASON_NO_QUALIFYING_OUTPUT)

        if not (usage_description or "").strip():
            return _fail(REASON_USAGE_DESCRIPTION_REQUIRED)
        if not confirmed:
            return _fail(REASON_CONFIRMATION_REQUIRED)

        result["ok"] = True
        return result

    def _resolve_knowledge_point(
        self, topic_id: int, topic_name: str, route_id: int | None
    ) -> dict:
        """确定性解析 Topic 的 knowledge_point（不写库）。

        status: existing / adopt / create / conflict
        """
        existing = self.evidence_repo.find_knowledge_point_by_topic(topic_id)
        if existing is not None:
            if existing.get("route_id") is not None and route_id is not None \
                    and int(existing["route_id"]) != int(route_id):
                return {"status": "conflict", "reason": REASON_KP_ROUTE_MISMATCH,
                        "kp_id": int(existing["id"])}
            return {"status": "existing", "reason": REASON_OK,
                    "kp_id": int(existing["id"])}
        clean = (topic_name or "").strip()
        if not clean:
            return {"status": "conflict", "reason": REASON_KP_CONFLICT,
                    "kp_id": None}
        by_name = self.evidence_repo.find_knowledge_point_by_name_route(
            clean, route_id
        )
        if by_name is not None:
            if by_name.get("topic_id") is None:
                return {"status": "adopt", "reason": REASON_OK,
                        "kp_id": int(by_name["id"])}
            return {"status": "conflict", "reason": REASON_KP_CONFLICT,
                    "kp_id": int(by_name["id"])}
        return {"status": "create", "reason": REASON_OK, "kp_id": None}

    # ================= 创建（事务原子） =================

    def create_project_topic_evidence(
        self,
        project_id: int,
        topic_id: int,
        output_ids: list[int],
        usage_description: str,
        confirmed: bool = False,
        revocation_note: str = "",
    ) -> dict:
        """创建 PracticeTopicEvidence + Output links + PROJECT CapabilityEvidence。

        整个流程在**同一事务**内；任何一步失败全部 rollback，
        不会出现“PracticeTopicEvidence 有了但 CapabilityEvidence 没有”。
        """
        check = self.validate_project_topic_evidence(
            project_id, topic_id, output_ids, usage_description, confirmed
        )
        if not check["ok"]:
            raise PracticeCapabilityError(
                reason_label(check["reason"]) or "无法形成项目能力证据",
                check["reason"],
            )
        topic = self.plan_repo.get_topic(topic_id)
        route_id = self.plan_repo.get_route_id_for_topic(topic_id)
        resolution = check["kp_resolution"]
        kp_id = check["knowledge_point_id"]
        selected_ids = [int(o["id"]) for o in check["outputs"]]

        try:
            self.conn.execute("BEGIN")
            if resolution == "create":
                kp = self.evidence_repo.insert_topic_linked_knowledge_point(
                    topic_id=int(topic_id), route_id=int(route_id),
                    name=topic.name, commit=False,
                )
                kp_id = int(kp["id"])
            elif resolution == "adopt":
                self.evidence_repo.link_knowledge_point_to_topic(
                    int(kp_id), int(topic_id), commit=False
                )
                kp_id = int(kp_id)
            evidence = self.evidence_repo.create(
                project_id=int(project_id), topic_id=int(topic_id),
                knowledge_point_id=int(kp_id),
                usage_description=(usage_description or "").strip(),
                commit=False,
            )
            for oid in selected_ids:
                self.evidence_repo.add_output(evidence["id"], oid, commit=False)
            # 唯一合法产生 Level 5 的路径
            self.capability_service.sync_from_practice_topic_evidence(
                evidence["id"], commit=False
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get_topic_evidence(evidence["id"])

    # ================= 撤销 =================

    def revoke_project_topic_evidence(
        self, evidence_id: int, reason: str = ""
    ) -> dict:
        """撤销原始事实 + 对应 capability evidence（同一事务，不物理删除）。"""
        evidence = self.evidence_repo.get(evidence_id)
        if evidence is None:
            raise PracticeCapabilityError(
                reason_label(REASON_EVIDENCE_NOT_FOUND), REASON_EVIDENCE_NOT_FOUND
            )
        try:
            self.conn.execute("BEGIN")
            self.evidence_repo.revoke(evidence_id, reason, commit=False)
            cap = self.capability_service.repo.get_by_key(
                evidence_key_for(evidence_id)
            )
            if cap is not None and cap["is_active"]:
                self.capability_service.repo.revoke(cap["id"], reason, commit=False)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get_topic_evidence(evidence_id)

    def revoke_by_capability_evidence(
        self, capability_evidence_id: int, reason: str = ""
    ) -> Optional[dict]:
        """通用 Evidence Dialog 的 revoke 入口（§60）。

        practice_project 类型必须路由到这里，保证原始事实同步撤销；
        其它类型走普通 capability revoke。
        """
        cap = self.capability_service.repo.get(capability_evidence_id)
        if cap is None:
            return None
        pte_id = cap.get("practice_topic_evidence_id")
        if pte_id:
            return self.revoke_project_topic_evidence(int(pte_id), reason)
        return self.capability_service.repo.revoke(
            int(capability_evidence_id), reason
        )

    def sync_capability_evidence(self, evidence_id: int) -> Optional[dict]:
        """幂等重算某条 PracticeTopicEvidence 对应的 capability evidence。"""
        return self.capability_service.sync_from_practice_topic_evidence(
            evidence_id
        )

    # ================= 展示辅助 =================

    def output_role_label(self, output: dict) -> str:
        return output_evidence_role(output)
