"""历史 route 归属修复（Phase E，幂等）。

只做**确定性可推导**的修复：
- topic-linked knowledge_point → topic 的 route；
- topic-linked task（generated new / manual linked）→ topic 的 route；
- 有 kp route 的 task（review / manual temp kp）→ kp 的 route。

绝不猜：
- manual kp（topic_id=NULL）的 route；
- ordinary manual todo（无 topic / 无 kp）。

返回修复条数，供启动日志。
"""

from __future__ import annotations

from ..utils.date_utils import now_iso


def repair_route_assignments(conn, assessment_repo=None) -> dict:
    result = {
        "kp_repaired": 0,
        "topic_task_repaired": 0,
        "kp_task_repaired": 0,
    }

    # 1) topic-linked kp 的 route
    if assessment_repo is not None:
        try:
            result["kp_repaired"] = \
                assessment_repo.ensure_knowledge_point_route_consistency()
        except Exception:  # noqa: BLE001
            result["kp_repaired"] = 0

    ts = now_iso()

    # 2) topic-linked task 的 route（只填/纠正可推导的）
    cur = conn.execute(
        "UPDATE tasks SET route_id = ("
        "  SELECT p.route_id FROM study_topics t "
        "  JOIN study_phases ph ON ph.id = t.phase_id "
        "  JOIN study_plans  p  ON p.id  = ph.plan_id "
        "  WHERE t.id = tasks.topic_id AND p.route_id IS NOT NULL"
        "), updated_at = ? "
        "WHERE topic_id IS NOT NULL AND EXISTS ("
        "  SELECT 1 FROM study_topics t "
        "  JOIN study_phases ph ON ph.id = t.phase_id "
        "  JOIN study_plans  p  ON p.id  = ph.plan_id "
        "  WHERE t.id = tasks.topic_id AND p.route_id IS NOT NULL"
        ") AND (route_id IS NULL OR route_id != ("
        "  SELECT p.route_id FROM study_topics t "
        "  JOIN study_phases ph ON ph.id = t.phase_id "
        "  JOIN study_plans  p  ON p.id  = ph.plan_id "
        "  WHERE t.id = tasks.topic_id))",
        (ts,),
    )
    result["topic_task_repaired"] = max(0, cur.rowcount)

    # 3) 有 kp route 的 task（review 等）
    cur = conn.execute(
        "UPDATE tasks SET route_id = ("
        "  SELECT kp.route_id FROM knowledge_points kp "
        "  WHERE kp.id = tasks.knowledge_point_id AND kp.route_id IS NOT NULL"
        "), updated_at = ? "
        "WHERE route_id IS NULL AND knowledge_point_id IS NOT NULL AND EXISTS ("
        "  SELECT 1 FROM knowledge_points kp "
        "  WHERE kp.id = tasks.knowledge_point_id AND kp.route_id IS NOT NULL"
        ")",
        (ts,),
    )
    result["kp_task_repaired"] = max(0, cur.rowcount)
    conn.commit()
    return result
