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
