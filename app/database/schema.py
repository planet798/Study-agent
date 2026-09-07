"""SQLite 数据表结构定义。

使用 executescript 一次性创建全部表和索引，全部幂等（IF NOT EXISTS）。

核心表 tasks 字段说明：
- id                  任务主键
- title               标题（必填）
- description         描述
- category            分类（学习 / 工作 / 生活 / 其他）
- estimated_minutes   预计时间（分钟）
- priority            优先级（1=低, 2=中, 3=高）
- status              状态（active=待办, done=已完成, not_done=已填写未完成）
- reason              未完成原因 / 延期原因
- scheduled_date      计划日期（YYYY-MM-DD），此即"所属哪一天"
- postpone_count      累计延期次数
- created_at          创建时间
- updated_at          最后更新时间
- completed_at        完成时间
- not_done_at         标记未完成的时间
"""

from __future__ import annotations

import sqlite3
from typing import Callable

# 状态常量
STATUS_ACTIVE = "active"
STATUS_DONE = "done"
STATUS_NOT_DONE = "not_done"
ALL_STATUS = (STATUS_ACTIVE, STATUS_DONE, STATUS_NOT_DONE)

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
SCHEMA_VERSION = 2

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
    create_schema(conn)

    current = get_schema_version(conn)
    if current == 0:
        # 兼容旧数据库：旧的 create_schema 不写版本号，但表已由 IF NOT EXISTS 保证
        current = 1
        _set_user_version(conn, current)

    for version in range(current + 1, SCHEMA_VERSION + 1):
        fn = _MIGRATIONS.get(version)
        if fn is None:
            raise RuntimeError(
                f"缺少数据库迁移：目标版本 v{version}（当前 SCHEMA_VERSION={SCHEMA_VERSION}）"
            )
        fn(conn)
        _set_user_version(conn, version)

    return get_schema_version(conn)
