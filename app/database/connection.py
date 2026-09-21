"""SQLite 数据库连接管理。

负责：
- 确定数据库文件路径（默认位于项目 data/ 目录）
- 打开连接并设置必要的 PRAGMA
- 确保表结构已创建
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .schema import migrate

# 项目根目录：app/database -> .. -> .. 为 study-agent/
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "study_agent.db"


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    """解析最终数据库文件路径（与 get_connection 使用同一规则）。"""
    return Path(db_path) if db_path is not None else DEFAULT_DB_PATH


def get_raw_connection(
    db_path: str | Path | None = None, *, read_only: bool = False
) -> sqlite3.Connection:
    """打开连接但**不自动迁移**（供发布/诊断工具使用）。

    - read_only=True：SQLite read-only URI（mode=ro）+ `PRAGMA query_only=ON`，
      双重保证零写入（inventory / verify / planner-diagnostic）；
    - read_only=False：普通可写连接，但不会执行 migrate（由调用方显式迁移）。
    """
    path = resolve_db_path(db_path)
    if read_only:
        if not Path(path).exists():
            raise FileNotFoundError(f"数据库不存在: {path}")
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        # 双保险：即使代码路径尝试写入也直接失败
        conn.execute("PRAGMA query_only = ON")
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_readonly_connection(
    db_path: str | Path | None = None
) -> sqlite3.Connection:
    """真正只读连接（mode=ro + query_only=ON）的显式入口。"""
    return get_raw_connection(db_path, read_only=True)


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """打开（并初始化）一个 SQLite 连接。

    :param db_path: 数据库文件路径，None 时使用默认 data/study_agent.db
    :return: 配置好的 sqlite3.Connection
    """
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL 提升并发读写体验；对单机桌面应用足够
    conn.execute("PRAGMA journal_mode = WAL")

    # 幂等建表 + 按序迁移（兼容旧数据库，不破坏历史数据）
    migrate(conn)
    return conn
