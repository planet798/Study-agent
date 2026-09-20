"""TopicLearningProfileService（Phase 2）。

课程结构（Topic → Learning Component）的唯一判断入口：
- Planner / fallback / Skill gate / RouteProgress / UI 都通过本 Service，
  不把判断散落到各处。

核心语义：
- 一个 component 完成 = 存在 `task.component_id = c.id AND status='done'`；
- `topic_curriculum_complete` = 所有 enabled+required components 都完成；
- 没有 component profile 的 Topic → 保持 legacy 语义（存在正式 done 学习任务
  即为 covered / complete）；
- curriculum complete ≠ mastery（mastery 仍只来自 Assessment）。

历史保护：
- 已有历史 task 的 component 不物理删除（改为 enabled=0）；
- system seed 只负责“首次初始化”，不覆盖用户后续修改。
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from ..database.topic_learning_repository import (
    TopicLearningComponentRepository,
)
from .learning_activity import (
    ACTIVITY_LABELS,
    ACTIVITY_THEORY,
    activity_label,
)

# 正式“新知识学习任务”判定（legacy coverage）
_FORMAL_TASK_TYPES = ("new",)


class ActivityProfileError(ValueError):
    """component profile 校验失败。"""


class TopicLearningProfileService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        component_repo: TopicLearningComponentRepository | None = None,
    ):
        self.conn = conn
        self.repo = component_repo or TopicLearningComponentRepository(conn)

    # ================= 读取 =================

    def get_components(self, topic_id: int) -> list[dict]:
        return self.repo.list_by_topic(topic_id)

    def has_profile(self, topic_id: int) -> bool:
        return self.repo.has_profile(topic_id)

    def get_component(self, component_id: int) -> Optional[dict]:
        return self.repo.get(component_id)

    def is_component_complete(self, component_id: int) -> bool:
        if self.repo.is_component_done(component_id):
            return True
        comp = self.repo.get(component_id)
        if comp is not None and comp["activity_kind"] == ACTIVITY_THEORY:
            # 保守 legacy：历史 done 正式任务（无 component）视为“至少完成理论学习”。
            # 绝不据此推断 code_reading / experiment / interview / practice。
            if comp["topic_id"] in self._legacy_theory_done_topic_ids():
                return True
        return False

    def _enabled_components(self, topic_id: int, required_only: bool = False):
        comps = [c for c in self.repo.list_by_topic(topic_id) if c["enabled"]]
        if required_only:
            comps = [c for c in comps if c["required"]]
        comps.sort(key=lambda c: (c["order_index"], c["id"]))
        return comps

    def list_incomplete_required_components(self, topic_id: int) -> list[dict]:
        return [
            c for c in self._enabled_components(topic_id, required_only=True)
            if not self.is_component_complete(c["id"])
        ]

    def get_next_required_component(self, topic_id: int) -> Optional[dict]:
        """下一个需要完成的 required component；curriculum complete 返回 None。

        没有 component profile 的 Topic 返回 None（由 Planner 走 legacy 语义）。
        """
        if not self.has_profile(topic_id):
            return None
        for c in self._enabled_components(topic_id, required_only=True):
            if not self.is_component_complete(c["id"]):
                return c
        return None

    def is_topic_curriculum_complete(self, topic_id: int) -> bool:
        """课程活动是否全部完成（≠ mastery）。"""
        comps = self.repo.list_by_topic(topic_id)
        if not comps:
            return self._legacy_topic_complete(topic_id)
        required = [c for c in comps if c["enabled"] and c["required"]]
        if not required:
            # 没有 required（例如用户把全部设为 optional）→ legacy 兜底
            return self._legacy_topic_complete(topic_id)
        return all(self.is_component_complete(c["id"]) for c in required)

    def get_component_status(self, topic_id: int) -> list[dict]:
        """给 UI 用的活动状态列表（含 enabled/required/complete/label）。"""
        out = []
        for c in self._enabled_components(topic_id):
            out.append({
                "component_id": c["id"],
                "activity_kind": c["activity_kind"],
                "label": activity_label(c["activity_kind"]),
                "enabled": c["enabled"],
                "required": c["required"],
                "order_index": c["order_index"],
                "complete": self.is_component_complete(c["id"]),
            })
        return out

    def activity_summary(self, topic_id: int) -> dict:
        comps = self._enabled_components(topic_id)
        required = [c for c in comps if c["required"]]
        optional = [c for c in comps if not c["required"]]
        return {
            "required_total": len(required),
            "required_done": sum(
                1 for c in required if self.is_component_complete(c["id"])
            ),
            "optional_total": len(optional),
            "optional_done": sum(
                1 for c in optional if self.is_component_complete(c["id"])
            ),
        }

    def route_activity_summary(self, route_id: int) -> dict:
        """某路线下所有 topic 的 activity 完成度聚合（不混入 mastery）。"""
        comps = self.repo.list_by_route(route_id)
        required = [c for c in comps if c["enabled"] and c["required"]]
        optional = [c for c in comps if c["enabled"] and not c["required"]]
        return {
            "required_total": len(required),
            "required_done": sum(
                1 for c in required if self.is_component_complete(c["id"])
            ),
            "optional_total": len(optional),
            "optional_done": sum(
                1 for c in optional if self.is_component_complete(c["id"])
            ),
        }

    # ================= legacy 语义 =================

    def _legacy_topic_complete(self, topic_id: int) -> bool:
        """无 profile 的 Topic：存在正式 done 学习任务即 covered。"""
        placeholders = ",".join("?" for _ in _FORMAL_TASK_TYPES)
        row = self.conn.execute(
            f"SELECT 1 FROM tasks WHERE topic_id = ? AND status = 'done' "
            f"AND task_type IN ({placeholders}) LIMIT 1",
            (int(topic_id), *_FORMAL_TASK_TYPES),
        ).fetchone()
        return row is not None

    def _done_formal_topic_ids(self) -> set[int]:
        placeholders = ",".join("?" for _ in _FORMAL_TASK_TYPES)
        rows = self.conn.execute(
            f"SELECT DISTINCT topic_id FROM tasks WHERE status = 'done' "
            f"AND topic_id IS NOT NULL AND task_type IN ({placeholders})",
            _FORMAL_TASK_TYPES,
        ).fetchall()
        return {int(r[0]) for r in rows}

    def _done_component_ids(self) -> set[int]:
        rows = self.conn.execute(
            "SELECT DISTINCT component_id FROM tasks "
            "WHERE status = 'done' AND component_id IS NOT NULL"
        ).fetchall()
        return {int(r[0]) for r in rows}

    def _legacy_theory_done_topic_ids(self) -> set[int]:
        """存在“done 正式学习任务且 component_id IS NULL”的 topic id。"""
        rows = self.conn.execute(
            "SELECT DISTINCT topic_id FROM tasks WHERE status = 'done' "
            "AND topic_id IS NOT NULL AND component_id IS NULL "
            "AND task_type IN ('new')"
        ).fetchall()
        return {int(r[0]) for r in rows}

    def curriculum_complete_topic_ids(
        self, topic_ids: set[int] | None = None
    ) -> set[int]:
        """返回 curriculum complete 的 topic id 集合（批量，避免 N+1）。

        - 有 profile：所有 enabled+required component 完成；
        - 无 profile（或没有 required）：存在正式 done 学习任务。
        """
        done_components = self._done_component_ids()
        legacy_theory_topics = self._legacy_theory_done_topic_ids()
        comps_by_topic: dict[int, list[dict]] = {}
        for c in self.repo.list_all():
            comps_by_topic.setdefault(int(c["topic_id"]), []).append(c)

        complete: set[int] = set()
        profiled_topics: set[int] = set()
        for topic_id, comps in comps_by_topic.items():
            profiled_topics.add(topic_id)
            if topic_ids is not None and topic_id not in topic_ids:
                continue
            required = [c for c in comps if c["enabled"] and c["required"]]

            def _done(comp: dict) -> bool:
                if comp["id"] in done_components:
                    return True
                return (
                    comp["activity_kind"] == ACTIVITY_THEORY
                    and topic_id in legacy_theory_topics
                )

            if required and all(_done(c) for c in required):
                complete.add(topic_id)

        legacy_done = self._done_formal_topic_ids()
        for tid in legacy_done:
            if tid in profiled_topics:
                continue
            if topic_ids is not None and tid not in topic_ids:
                continue
            complete.add(tid)
        return complete

    # ================= 写入 / seed =================

    def ensure_default_profile(self, topic_id: int) -> list[dict]:
        """没有 profile 时创建最小安全闭环：theory enabled+required。"""
        if self.repo.has_profile(topic_id):
            return self.repo.list_by_topic(topic_id)
        self.repo.create(
            topic_id, ACTIVITY_THEORY, enabled=True, required=True, order_index=1
        )
        return self.repo.list_by_topic(topic_id)

    def ensure_profile_from_spec(
        self, topic_id: int, spec: list[tuple[str, bool]]
    ) -> list[dict]:
        """首次初始化 profile；已有 components 则**不覆盖**。

        :param spec: [(activity_kind, required), ...]（全部 enabled）
        """
        if self.repo.has_profile(topic_id):
            return self.repo.list_by_topic(topic_id)
        if not spec:
            return self.ensure_default_profile(topic_id)
        for idx, (kind, required) in enumerate(spec):
            self.repo.create(
                topic_id, kind, enabled=True, required=bool(required),
                order_index=idx + 1,
            )
        return self.repo.list_by_topic(topic_id)

    def set_component_enabled(self, component_id: int, enabled: bool) -> dict:
        comp = self.repo.get(component_id)
        if comp is None:
            raise ActivityProfileError(f"学习活动不存在: id={component_id}")
        if not enabled:
            if self.repo.has_active_linked_tasks(component_id):
                raise ActivityProfileError(
                    "该学习活动存在未完成任务，不能停用；请先完成或取消任务。"
                )
            self._assert_keeps_one_required(comp["topic_id"], exclude_id=component_id)
        return self.repo.set_enabled(component_id, enabled) or comp

    def set_component_required(self, component_id: int, required: bool) -> dict:
        comp = self.repo.get(component_id)
        if comp is None:
            raise ActivityProfileError(f"学习活动不存在: id={component_id}")
        if not required:
            self._assert_keeps_one_required(comp["topic_id"], exclude_id=component_id)
        return self.repo.set_required(component_id, required) or comp

    def reorder_components(self, topic_id: int, ordered_ids: list[int]) -> None:
        for idx, cid in enumerate(ordered_ids):
            self.repo.set_order(cid, idx + 1)

    def set_profile_state(
        self,
        topic_id: int,
        state: dict[str, tuple[bool, bool]],
        ordered_kinds: list[str],
    ) -> None:
        """按 UI 提交的完整状态更新 profile（先校验后应用，保证原子语义）。

        :param state: {activity_kind: (enabled, required)}
        :param ordered_kinds: UI 中的展示/推进顺序
        """
        from .learning_activity import is_valid_activity_kind

        for kind in state:
            if not is_valid_activity_kind(kind):
                raise ActivityProfileError(f"非法 activity_kind: {kind!r}")
        if not any(en and req for en, req in state.values()):
            raise ActivityProfileError(
                "每个 Topic 至少需要保留一个“启用且必需”的学习活动。"
            )
        # 校验停用的已有 component 是否存在未完成任务
        for kind, (enabled, _required) in state.items():
            if enabled:
                continue
            comp = self.repo.get_by_topic_and_kind(topic_id, kind)
            if comp is not None and self.repo.has_active_linked_tasks(comp["id"]):
                raise ActivityProfileError(
                    f"学习活动「{activity_label(kind)}」存在未完成任务，"
                    "不能停用；请先完成或取消任务。"
                )
        # 应用
        for order, kind in enumerate(ordered_kinds, 1):
            enabled, required = state.get(kind, (False, False))
            comp = self.repo.get_by_topic_and_kind(topic_id, kind)
            if comp is None:
                if enabled:
                    self.repo.create(
                        topic_id, kind, enabled=True, required=required,
                        order_index=order,
                    )
            else:
                self.repo.update(
                    comp["id"], enabled=enabled, required=required,
                    order_index=order,
                )

    def _assert_keeps_one_required(
        self, topic_id: int, exclude_id: int | None = None
    ) -> None:
        remaining = [
            c for c in self.repo.list_by_topic(topic_id)
            if c["enabled"] and c["required"] and c["id"] != exclude_id
        ]
        if not remaining:
            raise ActivityProfileError(
                "每个 Topic 至少需要保留一个“启用且必需”的学习活动。"
            )

    # ================= 历史 backfill / 一致性 =================

    def backfill_legacy_theory(self) -> dict:
        """保守 backfill：为已有 profile 的 Topic 绑定历史 done 正式任务到 theory。

        只绑定“最早的一条”历史 done 正式任务（component_id IS NULL）到 theory，
        并设置 learning_activity_kind='theory'。
        **绝不**根据标题猜 code_reading / experiment / interview / practice。
        """
        stats = {
            "topics_profiled": 0,
            "legacy_theory_backfilled": 0,
            "tasks_linked": 0,
            "skipped_no_done_task": 0,
            "skipped_ambiguous": 0,
        }
        comps_by_topic: dict[int, list[dict]] = {}
        for c in self.repo.list_all():
            comps_by_topic.setdefault(int(c["topic_id"]), []).append(c)
        stats["topics_profiled"] = len(comps_by_topic)

        for topic_id, comps in comps_by_topic.items():
            theory = next(
                (c for c in comps
                 if c["activity_kind"] == ACTIVITY_THEORY and c["enabled"]),
                None,
            )
            if theory is None:
                continue
            if self.repo.is_component_done(theory["id"]):
                continue  # 已严格绑定完成，无需 backfill
            row = self.conn.execute(
                "SELECT id FROM tasks WHERE topic_id = ? AND status = 'done' "
                "AND component_id IS NULL AND task_type IN ('new') "
                "ORDER BY id ASC LIMIT 1",
                (topic_id,),
            ).fetchone()
            if row is None:
                stats["skipped_no_done_task"] += 1
                stats["skipped_ambiguous"] += 1
                continue
            self.conn.execute(
                "UPDATE tasks SET component_id = ?, "
                "learning_activity_kind = 'theory' WHERE id = ?",
                (theory["id"], int(row[0])),
            )
            self.conn.commit()
            stats["legacy_theory_backfilled"] += 1
            stats["tasks_linked"] += 1
        return stats

    def validate_consistency(self) -> list[dict]:
        """检查 task.component_id 与 component / topic / route 的一致性。"""
        rows = self.conn.execute(
            "SELECT t.id AS task_id, t.topic_id, t.route_id, t.component_id,"
            " t.learning_activity_kind, c.topic_id AS c_topic,"
            " c.activity_kind AS c_kind FROM tasks t "
            "JOIN topic_learning_components c ON c.id = t.component_id "
            "WHERE t.component_id IS NOT NULL"
        ).fetchall()
        conflicts: list[dict] = []
        for r in rows:
            issues = []
            if r["topic_id"] != r["c_topic"]:
                issues.append("component_topic_mismatch")
            if r["learning_activity_kind"] != r["c_kind"]:
                issues.append("activity_kind_mismatch")
            if issues:
                conflicts.append({
                    "task_id": int(r["task_id"]),
                    "component_id": int(r["component_id"]),
                    "issues": issues,
                })
        # dangling component_id（component 不存在）
        dangling = self.conn.execute(
            "SELECT id, component_id FROM tasks WHERE component_id IS NOT NULL "
            "AND component_id NOT IN "
            "(SELECT id FROM topic_learning_components)"
        ).fetchall()
        for r in dangling:
            conflicts.append({
                "task_id": int(r["id"]),
                "component_id": int(r["component_id"]),
                "issues": ["component_missing"],
            })
        return conflicts

    def repair_consistency(self) -> dict:
        """修复可确定性推导的元数据不一致（不改 status / mastery / 时间）。"""
        stats = {"activity_kind_fixed": 0, "dangling_component_cleared": 0}
        rows = self.conn.execute(
            "SELECT t.id, t.learning_activity_kind, c.activity_kind "
            "FROM tasks t JOIN topic_learning_components c "
            "ON c.id = t.component_id "
            "WHERE t.component_id IS NOT NULL "
            "AND (t.learning_activity_kind IS NULL "
            "OR t.learning_activity_kind != c.activity_kind)"
        ).fetchall()
        for r in rows:
            self.conn.execute(
                "UPDATE tasks SET learning_activity_kind = ? WHERE id = ?",
                (r[2], int(r[0])),
            )
            stats["activity_kind_fixed"] += 1
        cur = self.conn.execute(
            "UPDATE tasks SET component_id = NULL, learning_activity_kind = NULL "
            "WHERE component_id IS NOT NULL AND component_id NOT IN "
            "(SELECT id FROM topic_learning_components)"
        )
        stats["dangling_component_cleared"] = cur.rowcount or 0
        self.conn.commit()
        return stats
