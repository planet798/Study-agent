"""SQLite 数据库连接管理。

负责：
- 确定数据库文件路径（默认位于项目 data/ 目录）
- 打开连接并设置必要的 PRAGMA
- 确保表结构已创建
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .schema import create_schema

# 项目根目录：app/database -> .. -> .. 为 study-agent/
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "study_agent.db"


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """打开（并初始化）一个 SQLite 连接。

    :param db_path: 数据库文件路径，None 时使用默认 data/study_agent.db
    :return: 配置好的 sqlite3.Connection
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL 提升并发读写体验；对单机桌面应用足够
    conn.execute("PRAGMA journal_mode = WAL")

    # 幂等创建表结构
    create_schema(conn)
    return conn
