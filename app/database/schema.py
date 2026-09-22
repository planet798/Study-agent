"""SQLite 数据表结构定义。

使用 executescript 一次性创建全部表和索引，全部幂等（IF NOT EXISTS）。

核心表 tasks 字段说明：
- id                  任务主键
- title               标题（必填）
- description         描述
- category            分类（学习 / 工作 / 生活 / 其他）
- estimated_minutes   预计时间（分钟）
- priority            优先级（1=低, 2=中, 3=高）
- status              状态（active=待办, done=已完成, not_done=已填写未完成,
                              cancelled=用户主动从今天计划移除，不等于未完成、不物理删除）
- reason              未完成原因 / 延期原因
- scheduled_date      计划日期（YYYY-MM-DD），此即"所属哪一天"
- postpone_count      累计延期次数
- created_at          创建时间
- updated_at          最后更新时间
- completed_at        完成时间
- not_done_at         标记未完成的时间
"""

from __future__ import annotations

import json
import sqlite3
from typing import Callable

# 状态常量
STATUS_ACTIVE = "active"
STATUS_DONE = "done"
STATUS_NOT_DONE = "not_done"
# cancelled：该任务原本安排在某天，但用户主动决定当天不执行。
# 语义：不是未完成（not_done）、不是完成（done）；保留历史记录，绝不物理删除。
STATUS_CANCELLED = "cancelled"
ALL_STATUS = (STATUS_ACTIVE, STATUS_DONE, STATUS_NOT_DONE, STATUS_CANCELLED)

# 优先级常量
PRIORITY_LOW = 1
PRIORITY_MEDIUM = 2
PRIORITY_HIGH = 3
ALL_PRIORITIES = (PRIORITY_LOW, PRIORITY_MEDIUM, PRIORITY_HIGH)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT    NOT NULL,
    description      TEXT    NOT NULL DEFAULT '',
    category         TEXT    NOT NULL DEFAULT '学习',
    estimated_minutes INTEGER NOT NULL DEFAULT 0,
    priority         INTEGER NOT NULL DEFAULT 1,
    status           TEXT    NOT NULL DEFAULT 'active',
    reason           TEXT,
    scheduled_date   TEXT    NOT NULL,
    postpone_count   INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,
    completed_at     TEXT,
    not_done_at      TEXT,
    source           TEXT    NOT NULL DEFAULT 'manual',
    topic_id         INTEGER
);

CREATE INDEX IF NOT EXISTS idx_tasks_scheduled_date ON tasks(scheduled_date);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_status_date ON tasks(status, scheduled_date);

-- 应用元数据（键值对），用于记录"最后处理日期"等状态
CREATE TABLE IF NOT EXISTS app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 长期学习计划：定义"这几个月学什么"
CREATE TABLE IF NOT EXISTS study_plans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    start_date  TEXT    NOT NULL,
    end_date    TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'active'
);

-- 学习阶段：定义"这一阶段学什么"（属于一个计划）
CREATE TABLE IF NOT EXISTS study_phases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id     INTEGER NOT NULL REFERENCES study_plans(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    start_date  TEXT    NOT NULL,
    end_date    TEXT    NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 1,
    goals       TEXT    NOT NULL DEFAULT ''
);

-- 学习主题：定义"具体要掌握什么"（属于一个阶段）
CREATE TABLE IF NOT EXISTS study_topics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    phase_id         INTEGER NOT NULL REFERENCES study_phases(id) ON DELETE CASCADE,
    name             TEXT    NOT NULL,
    description      TEXT    NOT NULL DEFAULT '',
    estimated_minutes INTEGER NOT NULL DEFAULT 30,
    priority         INTEGER NOT NULL DEFAULT 1,
    order_index      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_study_phases_plan  ON study_phases(plan_id);
CREATE INDEX IF NOT EXISTS idx_study_topics_phase ON study_topics(phase_id);
CREATE INDEX IF NOT EXISTS idx_study_phases_dates ON study_phases(start_date, end_date);

-- AI 规划决策记录：用于分析"AI 为什么这么安排任务"
CREATE TABLE IF NOT EXISTS planner_decisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    date             TEXT    NOT NULL,
    current_phase_id INTEGER,
    input_context    TEXT    NOT NULL,
    ai_response      TEXT    NOT NULL,
    accepted_tasks   TEXT    NOT NULL,
    source           TEXT    NOT NULL DEFAULT 'ai',
    created_at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_planner_decisions_date ON planner_decisions(date);

-- 周/月总结缓存：统计经本地计算，AI 总结可缓存复用
CREATE TABLE IF NOT EXISTS weekly_summaries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    stats_json    TEXT NOT NULL,
    ai_summary_json TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT 'local',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_weekly_summaries_period ON weekly_summaries(period_start, period_end);

CREATE TABLE IF NOT EXISTS monthly_summaries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    stats_json    TEXT NOT NULL,
    ai_summary_json TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT 'local',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_monthly_summaries_period ON monthly_summaries(period_start, period_end);
"""


def create_schema(conn) -> None:
    """在给定的 sqlite3 连接上执行建表语句（幂等）。"""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


# ============================================================
# 数据库迁移机制
# ============================================================

# 当前数据库结构版本（通过 SQLite 的 PRAGMA user_version 持久化）。
# 旧数据库（此机制引入之前创建的）user_version = 0，被视为 v1：
# 其基础表已由上方 SCHEMA_SQL 中的 CREATE TABLE IF NOT EXISTS 幂等保证。
SCHEMA_VERSION = 20

# 迁移动态表：{目标版本: 迁移函数}。
# 以后新增表/字段时：
#   1) 新增一个迁移函数并登记到 _MIGRATIONS[v]；
#   2) 把 SCHEMA_VERSION 提到 v；
#   3) 迁移函数自身必须幂等（IF NOT EXISTS / add_column_if_not_exists）。
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {}


# ============================================================
# v2：知识点 + 验收记录（Phase 3B）
# ============================================================
#
# knowledge_points：知识点级档案（比 study_topics 更细）。
#   mastery_estimate 是系统根据真实验收证据计算的内部估计，不是用户自评。
#
# assessment_attempts：一次验收的题目 + 用户原始答案 + AI 判定结果。
#   questions/answers/ai_result 均以 JSON 保存，保证可审计。

_V2_SQL = """
CREATE TABLE IF NOT EXISTS knowledge_points (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id          INTEGER,
    name              TEXT    NOT NULL UNIQUE,
    description       TEXT    NOT NULL DEFAULT '',
    first_learned_at  TEXT,
    last_assessed_at  TEXT,
    mastery_estimate  REAL    NOT NULL DEFAULT 0.0,
    review_count      INTEGER NOT NULL DEFAULT 0,
    next_review_date  TEXT,
    interval_days     INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_points_topic
    ON knowledge_points(topic_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_points_review
    ON knowledge_points(next_review_date);

CREATE TABLE IF NOT EXISTS assessment_attempts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_point_id INTEGER NOT NULL,
    task_id            INTEGER,
    questions_json     TEXT    NOT NULL,
    answers_json       TEXT    NOT NULL DEFAULT '',
    ai_result_json     TEXT    NOT NULL DEFAULT '',
    mastery_estimate   REAL,
    result_level       TEXT,
    weak_points_json   TEXT    NOT NULL DEFAULT '',
    created_at         TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assessment_attempts_kp
    ON assessment_attempts(knowledge_point_id);
CREATE INDEX IF NOT EXISTS idx_assessment_attempts_created
    ON assessment_attempts(created_at);
"""


def _migrate_v2(conn: sqlite3.Connection) -> None:
    """v2：创建知识点与验收记录表（幂等）。"""
    conn.executescript(_V2_SQL)
    conn.commit()


_MIGRATIONS[2] = _migrate_v2


# ============================================================
# v3：验收判题状态（Phase 3D）
# ============================================================
#
# 在 assessment_attempts 上增加判题状态，区分：
#   pending 等待判题 / judged 已判题 / failed 判题失败（可稍后重判）。

def _migrate_v3(conn: sqlite3.Connection) -> None:
    """v3：adding judge_status / judge_error（幂等）。"""
    add_column_if_not_exists(
        conn,
        "assessment_attempts",
        "judge_status",
        "TEXT NOT NULL DEFAULT 'pending'",
    )
    add_column_if_not_exists(
        conn,
        "assessment_attempts",
        "judge_error",
        "TEXT NOT NULL DEFAULT ''",
    )


_MIGRATIONS[3] = _migrate_v3


# ============================================================
# v4：复习调度（Phase 4）
# ============================================================
#
# - tasks 增加 knowledge_point_id / task_type，区分新知识任务与复习任务；
# - review_schedule 记录“某知识点某天到期需要复习”及其间隔/状态。

_V4_SQL = """
CREATE TABLE IF NOT EXISTS review_schedule (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_point_id INTEGER NOT NULL,
    scheduled_date     TEXT    NOT NULL,
    interval_days      INTEGER NOT NULL,
    status             TEXT    NOT NULL DEFAULT 'pending',
    task_id            INTEGER,
    source_attempt_id  INTEGER,
    created_at         TEXT    NOT NULL,
    completed_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_schedule_kp
    ON review_schedule(knowledge_point_id);
CREATE INDEX IF NOT EXISTS idx_review_schedule_date
    ON review_schedule(scheduled_date);
CREATE INDEX IF NOT EXISTS idx_review_schedule_status
    ON review_schedule(status);
"""


def _migrate_v4(conn: sqlite3.Connection) -> None:
    """v4：tasks 加列 + 新建 review_schedule（幂等）。"""
    add_column_if_not_exists(conn, "tasks", "task_type", "TEXT NOT NULL DEFAULT 'new'")
    add_column_if_not_exists(conn, "tasks", "knowledge_point_id", "INTEGER")
    conn.executescript(_V4_SQL)
    conn.commit()


_MIGRATIONS[4] = _migrate_v4


# ============================================================
# v5：额外学习任务难度（Phase 5）
# ============================================================


def _migrate_v5(conn: sqlite3.Connection) -> None:
    """v5：tasks 增加 difficulty（基础巩固 basic / 实践 practice / 挑战 challenge）。"""
    add_column_if_not_exists(
        conn,
        "tasks",
        "difficulty",
        "TEXT NOT NULL DEFAULT 'practice'",
    )


_MIGRATIONS[5] = _migrate_v5


# ============================================================
# v6：技能 / JD / 学习成果 底座（Phase A）
# ============================================================
#
# - skills            : 技能实体（S/A/B/C tier、状态、JD 频率、
#                       系统计算的优先级、前置依赖、主题映射、共振标记）
# - jds               : 真实 JD 记录（保留原文 raw_text + 结构化 parsed）
# - learning_outcomes : 学习成果 → 简历素材底座
#
# 本阶段只建设数据结构；优先级计算在 app/services/skill_service.py（确定性规则）。

_V6_SQL = """
CREATE TABLE IF NOT EXISTS skills (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL UNIQUE,
    tier             TEXT    NOT NULL DEFAULT 'C',
    category         TEXT    NOT NULL DEFAULT 'core',
    status           TEXT    NOT NULL DEFAULT 'not_started',
    mastery_ref      TEXT    NOT NULL DEFAULT '',
    jd_frequency     TEXT    NOT NULL DEFAULT '{}',
    priority_score   REAL    NOT NULL DEFAULT 0.0,
    prerequisites    TEXT    NOT NULL DEFAULT '[]',
    linked_topics    TEXT    NOT NULL DEFAULT '[]',
    shared_connector INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL DEFAULT '',
    updated_at       TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_skills_tier   ON skills(tier);
CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);

CREATE TABLE IF NOT EXISTS jds (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    company            TEXT    NOT NULL DEFAULT '',
    title              TEXT    NOT NULL DEFAULT '',
    direction          TEXT    NOT NULL DEFAULT '',
    intern_requirement TEXT    NOT NULL DEFAULT '',
    raw_text           TEXT    NOT NULL DEFAULT '',
    parsed             TEXT    NOT NULL DEFAULT '{}',
    uploaded_at        TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jds_direction ON jds(direction);

CREATE TABLE IF NOT EXISTS learning_outcomes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL DEFAULT '',
    kind            TEXT    NOT NULL DEFAULT 'note',
    title           TEXT    NOT NULL DEFAULT '',
    content         TEXT    NOT NULL DEFAULT '',
    tech_stack      TEXT    NOT NULL DEFAULT '[]',
    dataset         TEXT    NOT NULL DEFAULT '',
    metrics         TEXT    NOT NULL DEFAULT '{}',
    git_commit      TEXT    NOT NULL DEFAULT '',
    github_url      TEXT    NOT NULL DEFAULT '',
    resume_keywords TEXT    NOT NULL DEFAULT '[]',
    linked_kp_id    INTEGER,
    linked_topic_id INTEGER,
    created_at      TEXT    NOT NULL DEFAULT '',
    updated_at      TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_learning_outcomes_date ON learning_outcomes(date);
CREATE INDEX IF NOT EXISTS idx_learning_outcomes_kind ON learning_outcomes(kind);
"""


def _migrate_v6(conn: sqlite3.Connection) -> None:
    """v6：创建 skills / jds / learning_outcomes 三张表（幂等）。"""
    conn.executescript(_V6_SQL)
    conn.commit()


_MIGRATIONS[6] = _migrate_v6


# ============================================================
# v7：jds 增加 content_hash（Phase B，幂等入库用）
# ============================================================
# 同一 JD 重复入库时以规范化文本哈希去重，避免 frequency 重复累计。


def _migrate_v7(conn: sqlite3.Connection) -> None:
    """v7：jds 增加 content_hash 列 + 索引（幂等）。"""
    add_column_if_not_exists(
        conn,
        "jds",
        "content_hash",
        "TEXT NOT NULL DEFAULT ''",
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jds_content_hash ON jds(content_hash)"
    )
    conn.commit()


_MIGRATIONS[7] = _migrate_v7


# ============================================================
# v8: learning_outcomes 增加来源（Phase D，幂等去重）
# ============================================================
# 记录“成果由哪个 task / 哪次 assessment 产生”，用于幂等：
# 同一 task/attempt 重复触发只更新或跳过，不重复生成新 outcome。


def _migrate_v8(conn: sqlite3.Connection) -> None:
    """v8：learning_outcomes 增加 task_id / source_attempt_id 列 + 索引（幂等）。"""
    add_column_if_not_exists(conn, "learning_outcomes", "task_id", "INTEGER")
    add_column_if_not_exists(
        conn, "learning_outcomes", "source_attempt_id", "INTEGER"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_learning_outcomes_task "
        "ON learning_outcomes(task_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_learning_outcomes_attempt "
        "ON learning_outcomes(source_attempt_id)"
    )
    conn.commit()


_MIGRATIONS[8] = _migrate_v8


# ============================================================
# v9：每日 JD 技术汇总（Step 4）
# ============================================================
# jd_daily_summaries：人工汇总的“某天看了 N 家岗位”的市场样本。
#   UNIQUE(summary_date, target_type) 保证同一天同一目标只有一份，
#   重新整理时走 update/replace，不会叠加造成 double count。
# jd_daily_skill_stats：该样本的技能统计。skill_id 可为空（未匹配技能），
#   raw_skill_name 永远保留用户原始写法。

_V9_SQL = """
CREATE TABLE IF NOT EXISTS jd_daily_summaries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_date TEXT    NOT NULL,
    target_type  TEXT    NOT NULL DEFAULT 'internship',
    sample_count INTEGER NOT NULL,
    raw_text     TEXT    NOT NULL DEFAULT '',
    note         TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    UNIQUE(summary_date, target_type)
);
CREATE INDEX IF NOT EXISTS idx_jd_daily_summaries_date
    ON jd_daily_summaries(summary_date, target_type);

CREATE TABLE IF NOT EXISTS jd_daily_skill_stats (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_id     INTEGER NOT NULL,
    skill_id       INTEGER,
    raw_skill_name TEXT    NOT NULL DEFAULT '',
    mention_count  INTEGER NOT NULL DEFAULT 0,
    must_count     INTEGER NOT NULL DEFAULT 0,
    plus_count     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(summary_id) REFERENCES jd_daily_summaries(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_jd_daily_stats_summary
    ON jd_daily_skill_stats(summary_id);
CREATE INDEX IF NOT EXISTS idx_jd_daily_stats_skill
    ON jd_daily_skill_stats(skill_id);
"""


def _migrate_v9(conn: sqlite3.Connection) -> None:
    """v9：创建每日 JD 汇总两张表（幂等）。"""
    conn.executescript(_V9_SQL)
    conn.commit()


_MIGRATIONS[9] = _migrate_v9


# ============================================================
# v10：JD 新技能候选（候选池，不等于正式 skill）
# ============================================================
# 近期 30 天 JD 中“无法通过明确 alias 映射到现有技能”的高频技术，先进入候选池，
# 由用户确认后才正式建 Skill。绝不让 raw string 直接进 skill_pool。
#   canonical_name   建议的规范名（UNIQUE）
#   raw_names        JSON 数组：出现过的原始写法
#   status           candidate / accepted / ignored

_V10_SQL = """
CREATE TABLE IF NOT EXISTS jd_skill_candidates (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name    TEXT    NOT NULL UNIQUE,
    raw_names         TEXT    NOT NULL DEFAULT '[]',
    mention_count_30d INTEGER NOT NULL DEFAULT 0,
    sample_count_30d  INTEGER NOT NULL DEFAULT 0,
    frequency_30d     REAL    NOT NULL DEFAULT 0,
    first_seen        TEXT,
    last_seen         TEXT,
    status            TEXT    NOT NULL DEFAULT 'candidate',
    accepted_skill_name TEXT  NOT NULL DEFAULT '',
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jd_skill_candidates_status
    ON jd_skill_candidates(status);
"""


def _migrate_v10(conn: sqlite3.Connection) -> None:
    """v10：创建 JD 新技能候选表（幂等）。"""
    conn.executescript(_V10_SQL)
    conn.commit()


_MIGRATIONS[10] = _migrate_v10


# ============================================================
# v11：Project-driven Learning 数据层支持（Phase 11 Step 1）
# ============================================================
# 为 tasks 增加项目化学习字段（均幂等加列，不破坏旧库/旧数据）：
#   project_name / project_repo / deliverable /
#   acceptance_criteria / expected_artifact
# task_type 不增加 CHECK 约束，保留 new/review/extra，后续可用 project/experiment。


def _migrate_v11(conn: sqlite3.Connection) -> None:
    """v11：tasks 增加项目化学习字段（幂等）。"""
    for column in (
        "project_name",
        "project_repo",
        "deliverable",
        "acceptance_criteria",
        "expected_artifact",
    ):
        add_column_if_not_exists(
            conn, "tasks", column, "TEXT NOT NULL DEFAULT ''"
        )


_MIGRATIONS[11] = _migrate_v11


# ============================================================
# v12：多学习路线数据基础（Phase B）
# ============================================================
#
# 目标：在不改变当前单路线行为的前提下，建立多路线数据层。
#
# 层级选择：仓库已存在真正的 study_plans 层，因此 route 挂在 plan 上层：
#   learning_routes -> study_plans -> study_phases -> study_topics
# 只给 study_plans 加 route_id，不给 phase/topic 重复存。
#
# 同时：
# - tasks.route_id            ：Task 自己知道所属路线（manual task 可能无 topic）
# - knowledge_points.route_id  ：manual knowledge 可能无 topic
# - planner_decisions.route_id ：未来多路线 Planner 必须区分同一天的不同 route
# - route_skills               ：LearningRoute N↔N Skill（skill 不复制）
#
# 迁移只自动创建：求职准备(group) → 搜广推 + LLM(learning)。
# 不自动创建 RL / C++ / 数据结构，也不替用户猜优先级。

_V12_SQL = """
CREATE TABLE IF NOT EXISTS learning_routes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id        INTEGER REFERENCES learning_routes(id) ON DELETE SET NULL,
    name             TEXT    NOT NULL,
    description      TEXT    NOT NULL DEFAULT '',
    goal             TEXT    NOT NULL DEFAULT '',
    route_type       TEXT    NOT NULL DEFAULT 'learning',
    status           TEXT    NOT NULL DEFAULT 'active',
    priority         INTEGER NOT NULL DEFAULT 3,
    planning_enabled INTEGER NOT NULL DEFAULT 1,
    source           TEXT    NOT NULL DEFAULT 'manual',
    created_at       TEXT    NOT NULL DEFAULT '',
    updated_at       TEXT    NOT NULL DEFAULT '',
    archived_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_learning_routes_parent
    ON learning_routes(parent_id);
CREATE INDEX IF NOT EXISTS idx_learning_routes_status
    ON learning_routes(status);

CREATE TABLE IF NOT EXISTS route_skills (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id   INTEGER NOT NULL REFERENCES learning_routes(id) ON DELETE CASCADE,
    skill_id   INTEGER NOT NULL,
    created_at TEXT    NOT NULL DEFAULT '',
    UNIQUE(route_id, skill_id)
);
CREATE INDEX IF NOT EXISTS idx_route_skills_route ON route_skills(route_id);
CREATE INDEX IF NOT EXISTS idx_route_skills_skill ON route_skills(skill_id);
"""

# 系统默认路线（迁移只自动创建这两条）
DEFAULT_ROUTE_PARENT_NAME = "求职准备"
DEFAULT_ROUTE_LEARNING_NAME = "搜广推 + LLM"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """判断表是否存在（迁移在部分建表的合成旧库上也要安全）。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _seed_default_routes(conn: sqlite3.Connection) -> int:
    """幂等创建 求职准备 → 搜广推 + LLM，返回默认 learning route id。"""
    row = conn.execute(
        "SELECT id FROM learning_routes WHERE name = ? AND parent_id IS NULL",
        (DEFAULT_ROUTE_PARENT_NAME,),
    ).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO learning_routes "
            "(parent_id, name, description, goal, route_type, status, priority,"
            " planning_enabled, source, created_at, updated_at, archived_at) "
            "VALUES (NULL, ?, '', '', 'group', 'active', 3, 0, 'system',"
            "        datetime('now','localtime'), datetime('now','localtime'), NULL)",
            (DEFAULT_ROUTE_PARENT_NAME,),
        )
        parent_id = cur.lastrowid
    else:
        parent_id = row[0]

    row = conn.execute(
        "SELECT id FROM learning_routes WHERE name = ? AND parent_id = ?",
        (DEFAULT_ROUTE_LEARNING_NAME, parent_id),
    ).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO learning_routes "
            "(parent_id, name, description, goal, route_type, status, priority,"
            " planning_enabled, source, created_at, updated_at, archived_at) "
            "VALUES (?, ?, '', '', 'learning', 'active', 3, 1, 'system',"
            "        datetime('now','localtime'), datetime('now','localtime'), NULL)",
            (parent_id, DEFAULT_ROUTE_LEARNING_NAME),
        )
        return cur.lastrowid
    return row[0]


def _backfill_v12_routes(conn: sqlite3.Connection, default_route_id: int) -> None:
    """回填历史数据的 route 归属（幂等，只填 NULL）。"""
    # 1) 现有 active study_plan 绑定默认 learning route
    conn.execute(
        "UPDATE study_plans SET route_id = ? "
        "WHERE route_id IS NULL AND status = 'active'",
        (default_route_id,),
    )

    has_kp = _table_exists(conn, "knowledge_points")

    # 2) topic-linked knowledge_points → 通过 topic/phase/plan 推导 route
    if has_kp:
        conn.execute(
            "UPDATE knowledge_points SET route_id = ("
            "  SELECT p.route_id FROM study_topics t "
            "  JOIN study_phases ph ON ph.id = t.phase_id "
            "  JOIN study_plans  p  ON p.id  = ph.plan_id "
            "  WHERE t.id = knowledge_points.topic_id AND p.route_id IS NOT NULL"
            ") WHERE route_id IS NULL AND topic_id IS NOT NULL AND EXISTS ("
            "  SELECT 1 FROM study_topics t "
            "  JOIN study_phases ph ON ph.id = t.phase_id "
            "  JOIN study_plans  p  ON p.id  = ph.plan_id "
            "  WHERE t.id = knowledge_points.topic_id AND p.route_id IS NOT NULL"
            ")"
        )

    # 3) tasks：先按 topic_id 推导
    conn.execute(
        "UPDATE tasks SET route_id = ("
        "  SELECT p.route_id FROM study_topics t "
        "  JOIN study_phases ph ON ph.id = t.phase_id "
        "  JOIN study_plans  p  ON p.id  = ph.plan_id "
        "  WHERE t.id = tasks.topic_id AND p.route_id IS NOT NULL"
        ") WHERE route_id IS NULL AND topic_id IS NOT NULL AND EXISTS ("
        "  SELECT 1 FROM study_topics t "
        "  JOIN study_phases ph ON ph.id = t.phase_id "
        "  JOIN study_plans  p  ON p.id  = ph.plan_id "
        "  WHERE t.id = tasks.topic_id AND p.route_id IS NOT NULL"
        ")"
    )
    # 4) tasks：按 knowledge_point.route_id 推导（review / manual knowledge）
    if has_kp:
        conn.execute(
            "UPDATE tasks SET route_id = ("
            "  SELECT kp.route_id FROM knowledge_points kp "
            "  WHERE kp.id = tasks.knowledge_point_id AND kp.route_id IS NOT NULL"
            ") WHERE route_id IS NULL AND knowledge_point_id IS NOT NULL AND EXISTS ("
            "  SELECT 1 FROM knowledge_points kp "
            "  WHERE kp.id = tasks.knowledge_point_id AND kp.route_id IS NOT NULL"
            ")"
        )
        # 5) tasks：kp 无 route 但 kp.topic_id 可推导
        conn.execute(
            "UPDATE tasks SET route_id = ("
            "  SELECT p.route_id FROM knowledge_points kp "
            "  JOIN study_topics t ON t.id = kp.topic_id "
            "  JOIN study_phases ph ON ph.id = t.phase_id "
            "  JOIN study_plans  p  ON p.id  = ph.plan_id "
            "  WHERE kp.id = tasks.knowledge_point_id AND p.route_id IS NOT NULL"
            ") WHERE route_id IS NULL AND knowledge_point_id IS NOT NULL AND EXISTS ("
            "  SELECT 1 FROM knowledge_points kp "
            "  JOIN study_topics t ON t.id = kp.topic_id "
            "  JOIN study_phases ph ON ph.id = t.phase_id "
            "  JOIN study_plans  p  ON p.id  = ph.plan_id "
            "  WHERE kp.id = tasks.knowledge_point_id AND p.route_id IS NOT NULL"
            ")"
        )

    # 6) 历史 planner_decisions 全部属于唯一正式路线
    conn.execute(
        "UPDATE planner_decisions SET route_id = ? WHERE route_id IS NULL",
        (default_route_id,),
    )

    # 7) route_skills：由 skills.linked_topics ∩ 该 route 的 topics 推导
    if _table_exists(conn, "skills"):
        route_topic_ids = {
            int(r[0])
            for r in conn.execute(
                "SELECT t.id FROM study_topics t "
                "JOIN study_phases ph ON ph.id = t.phase_id "
                "JOIN study_plans  p  ON p.id  = ph.plan_id "
                "WHERE p.route_id = ?",
                (default_route_id,),
            ).fetchall()
        }
        if route_topic_ids:
            for skill in conn.execute(
                "SELECT id, linked_topics FROM skills"
            ).fetchall():
                try:
                    linked = json.loads(skill[1] or "[]")
                except (TypeError, ValueError):
                    linked = []
                if not any(int(t) in route_topic_ids for t in linked):
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO route_skills "
                    "(route_id, skill_id, created_at) "
                    "VALUES (?, ?, datetime('now','localtime'))",
                    (default_route_id, int(skill[0])),
                )


def _migrate_v12(conn: sqlite3.Connection) -> None:
    """v12：多学习路线数据层 + 现有单路线数据迁移（幂等、无损）。"""
    conn.executescript(_V12_SQL)
    if _table_exists(conn, "study_plans"):
        add_column_if_not_exists(conn, "study_plans", "route_id", "INTEGER")
    add_column_if_not_exists(conn, "tasks", "route_id", "INTEGER")
    if _table_exists(conn, "knowledge_points"):
        add_column_if_not_exists(conn, "knowledge_points", "route_id", "INTEGER")
    if _table_exists(conn, "planner_decisions"):
        add_column_if_not_exists(conn, "planner_decisions", "route_id", "INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_route ON tasks(route_id)"
    )
    if _table_exists(conn, "study_plans"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_study_plans_route "
            "ON study_plans(route_id)"
        )
    if _table_exists(conn, "knowledge_points"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_points_route "
            "ON knowledge_points(route_id)"
        )
    if _table_exists(conn, "planner_decisions"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_planner_decisions_route "
            "ON planner_decisions(route_id)"
        )
    conn.commit()

    default_route_id = _seed_default_routes(conn)
    conn.commit()
    _backfill_v12_routes(conn, default_route_id)
    conn.commit()


_MIGRATIONS[12] = _migrate_v12


# ============================================================
# v13：路线结构编辑支持（Phase C）
# ============================================================
#
# 1) study_phases.order_index：手动路线按“阶段顺序”推进，而不是依赖绝对日期
#    （旧路线全部为 0，排序仍以 start_date 为主，行为不变）。
# 2) knowledge_points 唯一性从 name 改为 (name, route_id)：
#    不同路线允许同名知识点（C++ 的“基础” vs RL 的“基础”），
#    同一路线同名仍幂等。route_id IS NULL 由 service 层做幂等检查。
#    SQLite 无法删列级 UNIQUE，因此安全重建表（保留 id 与全部历史行）。

_KNOWLEDGE_POINTS_V13_SQL = """
CREATE TABLE knowledge_points (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id          INTEGER,
    route_id          INTEGER,
    name              TEXT    NOT NULL,
    description       TEXT    NOT NULL DEFAULT '',
    first_learned_at  TEXT,
    last_assessed_at  TEXT,
    mastery_estimate  REAL    NOT NULL DEFAULT 0.0,
    review_count      INTEGER NOT NULL DEFAULT 0,
    next_review_date  TEXT,
    interval_days     INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL,
    UNIQUE(name, route_id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_points_topic
    ON knowledge_points(topic_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_points_review
    ON knowledge_points(next_review_date);
CREATE INDEX IF NOT EXISTS idx_knowledge_points_route
    ON knowledge_points(route_id);
"""

_KNOWLEDGE_POINT_COLUMNS = (
    "id", "topic_id", "route_id", "name", "description", "first_learned_at",
    "last_assessed_at", "mastery_estimate", "review_count", "next_review_date",
    "interval_days", "created_at", "updated_at",
)


def _knowledge_points_has_route_unique(conn: sqlite3.Connection) -> bool:
    """新表定义是否已是 UNIQUE(name, route_id)。"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='knowledge_points'"
    ).fetchone()
    if row is None or not row[0]:
        return False
    normalized = " ".join(row[0].split()).lower().replace(" ", "")
    return "unique(name,route_id)" in normalized


def _migrate_v13(conn: sqlite3.Connection) -> None:
    """v13：phase order_index + knowledge_points 按 (name, route_id) 唯一（幂等）。"""
    if _table_exists(conn, "study_phases"):
        add_column_if_not_exists(
            conn, "study_phases", "order_index", "INTEGER NOT NULL DEFAULT 0"
        )

    if _table_exists(conn, "knowledge_points") and not \
            _knowledge_points_has_route_unique(conn):
        # 重建知识表（保留 id 与全部历史行/验收证据）
        conn.execute(
            "ALTER TABLE knowledge_points RENAME TO knowledge_points_old"
        )
        for idx in (
            "idx_knowledge_points_topic",
            "idx_knowledge_points_review",
            "idx_knowledge_points_route",
        ):
            conn.execute(f"DROP INDEX IF EXISTS {idx}")
        conn.executescript(_KNOWLEDGE_POINTS_V13_SQL)
        cols = ", ".join(_KNOWLEDGE_POINT_COLUMNS)
        conn.execute(
            f"INSERT INTO knowledge_points ({cols}) SELECT {cols} "
            "FROM knowledge_points_old"
        )
        conn.execute("DROP TABLE knowledge_points_old")
        conn.commit()


_MIGRATIONS[13] = _migrate_v13


# ============================================================
# v14：AI 设置中心（Phase AI Settings）
# ============================================================
#
# ai_profiles：用户在 UI 中维护的 API 配置（Provider Profile）。
#   **绝不存 API Key**：只保存 secret_ref（指向 keyring 中的条目）。
#   is_active 全局最多只有一个为 1（由 AIConfigService.set_active 保证）。
# prompt_overrides：用户对内置 Prompt 的自定义覆盖。
#   代码中的 default_template 始终是系统默认；用户修改只写 override；
#   “恢复默认” = DELETE 该行。
#   prompt_key UNIQUE 保证每个 Prompt 只有一份覆盖。

_V14_SQL = """
CREATE TABLE IF NOT EXISTS ai_profiles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name  TEXT    NOT NULL,
    provider_type TEXT    NOT NULL DEFAULT 'openai_compatible',
    base_url      TEXT    NOT NULL DEFAULT '',
    model         TEXT    NOT NULL DEFAULT '',
    secret_ref    TEXT    NOT NULL DEFAULT '',
    is_active     INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT '',
    updated_at    TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ai_profiles_active
    ON ai_profiles(is_active);

CREATE TABLE IF NOT EXISTS prompt_overrides (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_key TEXT    NOT NULL UNIQUE,
    content    TEXT    NOT NULL,
    updated_at TEXT    NOT NULL DEFAULT ''
);
"""


def _migrate_v14(conn: sqlite3.Connection) -> None:
    """v14：创建 AI Profile / Prompt Override 两张表（幂等，不含任何 Key）。"""
    conn.executescript(_V14_SQL)
    conn.commit()


_MIGRATIONS[14] = _migrate_v14


# ============================================================
# v15：六技术路线 canonical route_key + active plan 唯一性
# ============================================================
#
# 1) learning_routes.route_key：系统路线的稳定身份（不依赖 display name）。
#    用户手路线 route_key 可为 NULL，因此用 partial unique index。
# 2) study_plans 同一 route 只能有一个 active plan：先检测重复并确定性收敛，
#    再加 partial unique index（route_id IS NOT NULL AND status='active'）。
#    历史 route_id IS NULL 的旧计划不在此约束内（保留兼容）。


def _dedupe_active_plans(conn: sqlite3.Connection) -> int:
    """同一 route 存在多个 active plan 时，确定性保留一个，其余置 archived。

    保留规则（可解释、确定）：
      1. 被更多 tasks 引用的 plan（通过其 topic 引用）；
      2. 否则 topic 更多的 plan；
      3. 否则 id 更大（更新）的 plan。

    :return: 被置为 archived 的 plan 数量。
    """
    if not _table_exists(conn, "study_plans"):
        return 0
    rows = conn.execute(
        "SELECT route_id, id FROM study_plans "
        "WHERE status = 'active' AND route_id IS NOT NULL "
        "ORDER BY route_id, id"
    ).fetchall()
    by_route: dict[int, list[int]] = {}
    for route_id, plan_id in rows:
        by_route.setdefault(int(route_id), []).append(int(plan_id))

    archived = 0
    for route_id, plan_ids in by_route.items():
        if len(plan_ids) <= 1:
            continue
        scored = []
        for plan_id in plan_ids:
            task_count = conn.execute(
                "SELECT COUNT(*) FROM tasks t "
                "JOIN study_topics tp ON tp.id = t.topic_id "
                "JOIN study_phases ph ON ph.id = tp.phase_id "
                "WHERE ph.plan_id = ?",
                (plan_id,),
            ).fetchone()[0]
            topic_count = conn.execute(
                "SELECT COUNT(*) FROM study_topics tp "
                "JOIN study_phases ph ON ph.id = tp.phase_id "
                "WHERE ph.plan_id = ?",
                (plan_id,),
            ).fetchone()[0]
            scored.append((int(task_count), int(topic_count), plan_id))
        # 分数高者胜；tie → id 大者胜
        scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        keep = scored[0][2]
        for _, _, plan_id in scored[1:]:
            if plan_id == keep:
                continue
            conn.execute(
                "UPDATE study_plans SET status = 'archived' WHERE id = ?",
                (plan_id,),
            )
            archived += 1
    if archived:
        conn.commit()
    return archived


def _migrate_v15(conn: sqlite3.Connection) -> None:
    """v15：route_key 稳定标识 + 同 route 唯一 active plan（幂等）。"""
    add_column_if_not_exists(conn, "learning_routes", "route_key", "TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_routes_route_key "
        "ON learning_routes(route_key) WHERE route_key IS NOT NULL"
    )
    conn.commit()

    _dedupe_active_plans(conn)
    if _table_exists(conn, "study_plans"):
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_study_plans_active_route "
            "ON study_plans(route_id) "
            "WHERE status = 'active' AND route_id IS NOT NULL"
        )
    conn.commit()


_MIGRATIONS[15] = _migrate_v15


# ============================================================
# v16：Topic Learning Activity Model（Phase 2）
# ============================================================
#
# topic_learning_components：一个 Topic 计划采用哪些学习方式（activity）。
#   activity_kind ∈ theory/code_reading/experiment/interview/practice
#   UNIQUE(topic_id, activity_kind)
#   enabled=0 表示当前计划不再要求，但保留历史 task 关系（不物理删除）。
#   required=1 表示课程完成的必要条件。
#
# tasks 增加：
#   component_id          FK → topic_learning_components.id（可为 NULL）
#   learning_activity_kind 本次活动实际采用的方式（可为 NULL）
#
# 不修改 task_type / source / status 既有语义。

_V16_SQL = """
CREATE TABLE IF NOT EXISTS topic_learning_components (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id      INTEGER NOT NULL,
    activity_kind TEXT    NOT NULL
                  CHECK (activity_kind IN
                         ('theory','code_reading','experiment','interview','practice')),
    enabled       INTEGER NOT NULL DEFAULT 1,
    required      INTEGER NOT NULL DEFAULT 1,
    order_index   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT '',
    updated_at    TEXT    NOT NULL DEFAULT '',
    UNIQUE(topic_id, activity_kind)
);
CREATE INDEX IF NOT EXISTS idx_topic_components_topic
    ON topic_learning_components(topic_id);
"""


def _migrate_v16(conn: sqlite3.Connection) -> None:
    """v16：topic_learning_components + tasks.component_id/activity_kind（幂等）。"""
    conn.executescript(_V16_SQL)
    add_column_if_not_exists(conn, "tasks", "component_id", "INTEGER")
    add_column_if_not_exists(
        conn, "tasks", "learning_activity_kind", "TEXT"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_component ON tasks(component_id)"
    )
    conn.commit()


_MIGRATIONS[16] = _migrate_v16


# ============================================================
# v17：Capability Evidence（Phase 3）
# ============================================================
#
# capability_evidence：从已有真实证据（Task / Assessment / experiment Outcome）
# 提取的“能力等级”账本。与 mastery 彻底分开：
#   - Level 0（UNLEARNED）不写库；current = MAX(active level) 或 0；
#   - 不存 route_id（evidence → knowledge_point → route_id）；
#   - 不存 practice_project_id（Phase 4 再 migration）；
#   - evidence_key UNIQUE 保证幂等；
#   - 保留 is_active/revoked_at/revocation_reason，禁止物理 DELETE。

_V17_SQL = """
CREATE TABLE IF NOT EXISTS capability_evidence (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_point_id   INTEGER NOT NULL,
    capability_level     INTEGER NOT NULL
                         CHECK (capability_level BETWEEN 1 AND 5),
    evidence_type        TEXT    NOT NULL,
    evidence_key         TEXT    NOT NULL UNIQUE,
    source_task_id       INTEGER,
    assessment_attempt_id INTEGER,
    learning_outcome_id  INTEGER,
    description          TEXT    NOT NULL DEFAULT '',
    details_json         TEXT    NOT NULL DEFAULT '{}',
    is_active            INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT    NOT NULL DEFAULT '',
    revoked_at           TEXT,
    revocation_reason    TEXT
);
CREATE INDEX IF NOT EXISTS idx_capability_evidence_kp
    ON capability_evidence(knowledge_point_id);
CREATE INDEX IF NOT EXISTS idx_capability_evidence_level
    ON capability_evidence(capability_level);
CREATE INDEX IF NOT EXISTS idx_capability_evidence_active
    ON capability_evidence(is_active);
"""


def _migrate_v17(conn: sqlite3.Connection) -> None:
    """v17：capability_evidence 表（幂等）。"""
    conn.executescript(_V17_SQL)
    conn.commit()


_MIGRATIONS[17] = _migrate_v17


# ============================================================
# v18：Practice / Project Layer（Phase 4）
# ============================================================
#
# 独立于 LearningRoute 的实践/项目层：
#   practice_projects N:N learning_routes / skills / study_topics
#   practice_milestones（项目里程碑）
#   practice_outputs（真实项目产物）
#
# 不修改 capability_evidence / mastery / review / task activity。
# 不自动创建任何用户 Project。

_V18_SQL = """
CREATE TABLE IF NOT EXISTS practice_projects (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL,
    description         TEXT    NOT NULL DEFAULT '',
    goal                TEXT    NOT NULL DEFAULT '',
    project_type        TEXT    NOT NULL DEFAULT 'other'
                        CHECK (project_type IN (
                          'kaggle','github','paper_reproduction','study_agent',
                          'llm_training','agent','recommendation_search',
                          'benchmark_evaluation','other')),
    status              TEXT    NOT NULL DEFAULT 'planned'
                        CHECK (status IN
                          ('planned','in_progress','completed','archived')),
    source              TEXT    NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual','imported','ai')),
    archived_from_status TEXT,
    started_at          TEXT,
    target_date         TEXT,
    completed_at        TEXT,
    created_at          TEXT    NOT NULL DEFAULT '',
    updated_at          TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_practice_projects_status
    ON practice_projects(status);

CREATE TABLE IF NOT EXISTS practice_project_routes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL
               REFERENCES practice_projects(id) ON DELETE CASCADE,
    route_id   INTEGER NOT NULL REFERENCES learning_routes(id),
    created_at TEXT    NOT NULL DEFAULT '',
    UNIQUE(project_id, route_id)
);
CREATE INDEX IF NOT EXISTS idx_practice_project_routes_project
    ON practice_project_routes(project_id);
CREATE INDEX IF NOT EXISTS idx_practice_project_routes_route
    ON practice_project_routes(route_id);

CREATE TABLE IF NOT EXISTS practice_project_skills (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL
               REFERENCES practice_projects(id) ON DELETE CASCADE,
    skill_id   INTEGER NOT NULL REFERENCES skills(id),
    created_at TEXT    NOT NULL DEFAULT '',
    UNIQUE(project_id, skill_id)
);
CREATE INDEX IF NOT EXISTS idx_practice_project_skills_project
    ON practice_project_skills(project_id);
CREATE INDEX IF NOT EXISTS idx_practice_project_skills_skill
    ON practice_project_skills(skill_id);

CREATE TABLE IF NOT EXISTS practice_project_topics (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL
               REFERENCES practice_projects(id) ON DELETE CASCADE,
    topic_id   INTEGER NOT NULL REFERENCES study_topics(id),
    created_at TEXT    NOT NULL DEFAULT '',
    UNIQUE(project_id, topic_id)
);
CREATE INDEX IF NOT EXISTS idx_practice_project_topics_project
    ON practice_project_topics(project_id);
CREATE INDEX IF NOT EXISTS idx_practice_project_topics_topic
    ON practice_project_topics(topic_id);

CREATE TABLE IF NOT EXISTS practice_milestones (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL
                 REFERENCES practice_projects(id) ON DELETE CASCADE,
    title        TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    status       TEXT    NOT NULL DEFAULT 'todo'
                 CHECK (status IN ('todo','in_progress','done')),
    order_index  INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT,
    created_at   TEXT    NOT NULL DEFAULT '',
    updated_at   TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_practice_milestones_project
    ON practice_milestones(project_id);

CREATE TABLE IF NOT EXISTS practice_outputs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL
                 REFERENCES practice_projects(id) ON DELETE CASCADE,
    output_type  TEXT    NOT NULL
                 CHECK (output_type IN (
                   'repository','code','result','benchmark','checkpoint',
                   'report','readme','demo','paper_reproduction','dataset',
                   'other')),
    title        TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    uri          TEXT,
    details_json TEXT    NOT NULL DEFAULT '{}',
    created_at   TEXT    NOT NULL DEFAULT '',
    updated_at   TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_practice_outputs_project
    ON practice_outputs(project_id);
"""


def _migrate_v18(conn: sqlite3.Connection) -> None:
    """v18：Practice / Project 六张表（幂等，仅新增）。"""
    conn.executescript(_V18_SQL)
    conn.commit()


_MIGRATIONS[18] = _migrate_v18


# ============================================================
# v19：Practice → Capability（Phase 5）
# ============================================================
#
# PracticeTopicEvidence 是原始事实实体：
#   “用户确认某 Topic 在某 Project 中被真实使用，并由哪些 Output 支撑”。
# CapabilityEvidence 只是根据该事实得出的 Level 5 结论：
#   capability_evidence.practice_topic_evidence_id → practice_topic_evidence.id
#
# 不修改 mastery / review / tasks / activity / scheduler。
# 不自动创建任何 PROJECT evidence（必须用户显式确认）。

_V19_SQL = """
CREATE TABLE IF NOT EXISTS practice_topic_evidence (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id         INTEGER NOT NULL REFERENCES practice_projects(id),
    topic_id           INTEGER NOT NULL REFERENCES study_topics(id),
    knowledge_point_id INTEGER NOT NULL REFERENCES knowledge_points(id),
    usage_description  TEXT    NOT NULL,
    is_active          INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT    NOT NULL DEFAULT '',
    revoked_at         TEXT,
    revocation_reason  TEXT
);
CREATE INDEX IF NOT EXISTS idx_practice_topic_evidence_project
    ON practice_topic_evidence(project_id);
CREATE INDEX IF NOT EXISTS idx_practice_topic_evidence_topic
    ON practice_topic_evidence(topic_id);
CREATE INDEX IF NOT EXISTS idx_practice_topic_evidence_kp
    ON practice_topic_evidence(knowledge_point_id);
CREATE INDEX IF NOT EXISTS idx_practice_topic_evidence_active
    ON practice_topic_evidence(is_active);
-- 一个 Project + Topic 只允许一个 active evidence；历史（revoked）行保留。
CREATE UNIQUE INDEX IF NOT EXISTS idx_practice_topic_evidence_active_unique
    ON practice_topic_evidence(project_id, topic_id) WHERE is_active = 1;

CREATE TABLE IF NOT EXISTS practice_topic_evidence_outputs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id INTEGER NOT NULL
                REFERENCES practice_topic_evidence(id) ON DELETE CASCADE,
    output_id   INTEGER NOT NULL
                REFERENCES practice_outputs(id) ON DELETE CASCADE,
    created_at  TEXT    NOT NULL DEFAULT '',
    UNIQUE(evidence_id, output_id)
);
CREATE INDEX IF NOT EXISTS idx_practice_topic_evidence_outputs_evidence
    ON practice_topic_evidence_outputs(evidence_id);
CREATE INDEX IF NOT EXISTS idx_practice_topic_evidence_outputs_output
    ON practice_topic_evidence_outputs(output_id);
"""


def _migrate_v19(conn: sqlite3.Connection) -> None:
    """v19：PracticeTopicEvidence + evidence→output 关系 + capability 引用列。"""
    conn.executescript(_V19_SQL)
    # capability_evidence 引用原始事实（NULL 表示非实践证据）
    add_column_if_not_exists(
        conn, "capability_evidence", "practice_topic_evidence_id", "INTEGER"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_capability_evidence_practice "
        "ON capability_evidence(practice_topic_evidence_id)"
    )
    conn.commit()


_MIGRATIONS[19] = _migrate_v19


# ============================================================
# v20：Practice Planner Feedback（Phase 6）
# ============================================================
#
# practice_topic_requirements：“为了推进项目，这个 Topic 至少需要什么能力”。
# 与 practice_project_topics（项目事实关联）语义不同，因此单独建表：
#   - 不把字段硬塞进 practice_project_topics；
#   - target_capability_level 只能是 1~4（禁止 PROJECT，避免循环依赖）；
#   - UNIQUE(project_id, topic_id)；用户取消用 is_active=0，不物理 DELETE。
#
# 不修改 tasks / mastery / review / capability_evidence / scheduler。
# 不自动创建任何 Requirement。

_V20_SQL = """
CREATE TABLE IF NOT EXISTS practice_topic_requirements (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id             INTEGER NOT NULL REFERENCES practice_projects(id),
    topic_id               INTEGER NOT NULL REFERENCES study_topics(id),
    target_capability_level INTEGER NOT NULL
                           CHECK (target_capability_level BETWEEN 1 AND 4),
    is_active              INTEGER NOT NULL DEFAULT 1,
    note                   TEXT    NOT NULL DEFAULT '',
    created_at             TEXT    NOT NULL DEFAULT '',
    updated_at             TEXT    NOT NULL DEFAULT '',
    UNIQUE(project_id, topic_id)
);
CREATE INDEX IF NOT EXISTS idx_practice_topic_requirements_project
    ON practice_topic_requirements(project_id);
CREATE INDEX IF NOT EXISTS idx_practice_topic_requirements_topic
    ON practice_topic_requirements(topic_id);
CREATE INDEX IF NOT EXISTS idx_practice_topic_requirements_active
    ON practice_topic_requirements(is_active);
-- 仅 active requirement 参与 Planner（partial index 供 readiness 快速查询）
CREATE INDEX IF NOT EXISTS idx_practice_topic_requirements_active_project
    ON practice_topic_requirements(project_id) WHERE is_active = 1;
"""


def _migrate_v20(conn: sqlite3.Connection) -> None:
    """v20：practice_topic_requirements（幂等，仅新增；不自动建 Requirement）。"""
    conn.executescript(_V20_SQL)
    conn.commit()


_MIGRATIONS[20] = _migrate_v20


def get_schema_version(conn) -> int:
    """读取当前数据库结构版本（PRAGMA user_version）。"""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _set_user_version(conn, version: int) -> None:
    """写入数据库结构版本；内部版本号，避免 SQL 注入。"""
    conn.execute(f"PRAGMA user_version = {int(version)}")
    conn.commit()


def add_column_if_not_exists(conn, table: str, column: str, ddl: str) -> bool:
    """幂等加列：列不存在时执行 ALTER TABLE ADD COLUMN。

    返回 True 表示本次确实执行了迁移，False 表示列已存在。
    注意：table/column 只允许来自内部常量，不做用户输入。
    """
    # table_info 返回 (cid, name, type, notnull, dflt_value, pk)，用下标避免依赖 row_factory
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column in existing:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    conn.commit()
    return True


def migrate(conn) -> int:
    """把数据库结构升级到 SCHEMA_VERSION（幂等、可安全重复调用）。

    顺序：
    1. 先执行基础建表（IF NOT EXISTS，保证 v1 表存在）；
    2. 旧库（user_version=0）标记为 v1；
    3. 按序执行 v(current+1) .. v(SCHEMA_VERSION) 的迁移；
    4. 返回最终版本。

    迁移失败时 user_version 停留在失败前版本，下次启动会因幂等迁移自动重试。
    """
    return migrate_stepwise(conn)


def migrate_stepwise(conn, target: int | None = None, on_step=None) -> int:
    """逐级迁移并在每级后调用 on_step(version)（供诊断/发布工具）。

    行为与 migrate() 完全一致，只是把每一步暴露出来以便打印/校验。
    """
    target = SCHEMA_VERSION if target is None else int(target)
    create_schema(conn)

    current = get_schema_version(conn)
    if current == 0:
        # 兼容旧数据库：旧的 create_schema 不写版本号，但表已由 IF NOT EXISTS 保证
        current = 1
        _set_user_version(conn, current)

    for version in range(current + 1, target + 1):
        fn = _MIGRATIONS.get(version)
        if fn is None:
            raise RuntimeError(
                f"缺少数据库迁移：目标版本 v{version}（当前 SCHEMA_VERSION={SCHEMA_VERSION}）"
            )
        fn(conn)
        _set_user_version(conn, version)
        if on_step is not None:
            on_step(version)

    return get_schema_version(conn)


# ============================================================
# Fresh-DB fast path（仅用于全新空库；绝不可用于 legacy / 正式库）
# ============================================================
#
# 背景：普通测试每次都会新建一个空库。若走 `migrate()`，会在空库上
# 逐级重放 v2..v20（共 19 步，每步都 commit/fsync），实测约 280ms，
# 而其中真正 CPU 只有十几毫秒。
#
# `initialize_fresh_database()` 用「当前 schema 快照」一次性建好 vN 结构，
# 不重放历史迁移。快照是在**运行时**从真实迁移路径 `migrate_stepwise()`
# 反推出来的（见 `_build_fresh_snapshot`），因此与 `migrate()` 的结果
# 严格一致、不会漂移。
#
# 明确禁止用途：
#   - 已有 production DB（哪怕只是 user_version 不对）
#   - legacy DB / 待升级 DB
#   - release migration / migration gate
#   - schema migration / verifier / WAL backup 等迁移测试
# 这些场景必须继续走 `migrate()` / `migrate_stepwise()` 真实路径。

_FRESH_SNAPSHOT = None


def _build_fresh_snapshot():
    """从真实迁移路径反推「全新 vN 库」的完整 DDL + 确定性种子数据。

    只在进程内构建一次。使用内存库，不触碰磁盘、不产生任何副作用。
    返回 (ddl_statements, seed_rows)，其中 seed_rows 为
    ``[(table_name, [row_tuple, ...]), ...]``。
    """
    mem = sqlite3.connect(":memory:")
    try:
        mem.execute("PRAGMA journal_mode = MEMORY")
        mem.execute("PRAGMA synchronous = OFF")
        migrate_stepwise(mem)

        ddl = [
            row[0]
            for row in mem.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
                "ORDER BY CASE type "
                "  WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, rowid"
            )
        ]
        tables = [
            row[0]
            for row in mem.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
            )
        ]
        seeds = []
        for table in tables:
            rows = mem.execute(f"SELECT * FROM {table}").fetchall()
            if rows:
                seeds.append((table, rows))
        return tuple(ddl), tuple(seeds)
    finally:
        mem.close()


def _fresh_snapshot():
    global _FRESH_SNAPSHOT
    if _FRESH_SNAPSHOT is None:
        _FRESH_SNAPSHOT = _build_fresh_snapshot()
    return _FRESH_SNAPSHOT


def is_empty_database(conn) -> bool:
    """判断连接指向的库是否是「全新空库」（无用户表且 user_version=0）。"""
    user_tables = conn.execute(
        "SELECT count(*) FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]
    return int(user_tables) == 0 and get_schema_version(conn) == 0


def initialize_fresh_database(conn) -> int:
    """**仅用于全新空库**的快速初始化：直接建当前完整 schema。

    - 前置条件：``is_empty_database(conn)`` 为真；否则抛 RuntimeError。
    - 行为：一次性执行当前 vN 结构 DDL + 迁移在空库上产生的确定性种子，
      并设置 ``PRAGMA user_version = SCHEMA_VERSION``。
    - **不重放**历史迁移（v2..vN 逐步逻辑）。
    - 与 ``migrate()`` 在空库上的最终状态严格一致（含 learning_routes 种子）。

    禁止用于已有 / legacy / production / release-migration 数据库。
    返回设置后的 schema 版本。
    """
    if not is_empty_database(conn):
        raise RuntimeError(
            "initialize_fresh_database 只能用于全新的空数据库；"
            "已有数据的库请使用 migrate()/migrate_stepwise()。"
        )

    ddl, seeds = _fresh_snapshot()
    started = False
    try:
        # 所有 DDL / 种子集中在同一个事务里，只 fsync 一次。
        conn.execute("BEGIN")
        started = True
        for statement in ddl:
            conn.execute(statement)
        for table, rows in seeds:
            placeholders = ",".join("?" * len(rows[0]))
            conn.executemany(
                f"INSERT INTO {table} VALUES ({placeholders})", rows
            )
        conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
        conn.commit()
        started = False
    except Exception:
        if started:
            conn.rollback()
        raise
    return get_schema_version(conn)
