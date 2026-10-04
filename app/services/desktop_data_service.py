"""安装版数据导入/升级：副本验证成功后才启用，源库不作迁移。"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path
from contextlib import contextmanager

from app.database.schema import SCHEMA_VERSION
from app.diagnostics import release_migration as release


class DesktopDataError(RuntimeError):
    """只包含可向用户展示的操作说明，不包含数据库/凭据正文。"""


@contextmanager
def _connection(path: Path):
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _safe_tree(source: Path, target: Path) -> None:
    if _is_link(source):
        raise DesktopDataError("导入目录包含链接，请先移除链接或联接点。")
    target.mkdir()
    for item in source.iterdir():
        if _is_link(item):
            raise DesktopDataError("导入目录包含链接，请先移除链接或联接点。")
        if item.is_dir():
            _safe_tree(item, target / item.name)
        elif item.is_file():
            shutil.copy2(item, target / item.name)
        else:
            raise DesktopDataError("导入目录包含不支持的文件类型。")


def _validated_database(path: Path) -> dict:
    with _connection(path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise DesktopDataError("数据库来自较新的版本，请先升级 Study Agent。")
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='tasks' AND type='table'").fetchone():
            raise DesktopDataError("所选文件不是有效的 Study Agent 学习数据库。")
        if release.integrity_check(conn) != ["ok"] or release.foreign_key_check(conn):
            raise DesktopDataError("数据库完整性检查失败，请从备份恢复。")
        return release.inventory(conn)


def prepare_database(path: Path) -> None:
    """只用于暂存副本。逐级迁移并验证历史保全，不改真实数据库。"""
    before = _validated_database(path)
    if before["schema_version"] < SCHEMA_VERSION:
        preflight = release.dry_run_on_copy(str(path))
        if not preflight.get("ok"):
            raise DesktopDataError("数据库升级预演失败，原数据已保留，请检查备份。")
        # 复用既有发布迁移流程，避免另造业务迁移规则。
        from app.main import _run_release_migrate

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            _run_release_migrate(conn, apply_capability=True, skip_preflight=True)
            conn.commit()
        finally:
            conn.close()
    with _connection(path) as conn:
        result = release.verify(conn, before=before)
    if not result.get("ok"):
        raise DesktopDataError("数据校验未通过，原数据库和历史记录已保留。")


def _copy_configuration(source: Path, staged: Path) -> None:
    from app.agent.mcp.config import load_mcp_config
    from app.agent.sandbox.config import load_sandbox_config
    from app.ai.long_term_context import load_long_term_context

    for name, loader in (("mcp_servers.json", load_mcp_config), ("sandbox.json", load_sandbox_config)):
        path = source / name
        if path.exists():
            if _is_link(path):
                raise DesktopDataError("本地配置不能使用链接文件。")
            try:
                loader(path)
            except (ValueError, OSError):
                raise DesktopDataError("本地配置无效，请修复后重新导入。") from None
            shutil.copy2(path, staged / name)
    context = source / "career_context.json"
    if not context.exists():
        context = source.parent / "docs/career_context.json"
    if context.exists():
        if _is_link(context) or _is_link(context.parent) or load_long_term_context(context) is None:
            raise DesktopDataError("学习上下文文件无效，请修复后重新导入。")
        shutil.copy2(context, staged / "career_context.json")


def import_legacy_data(source: Path, destination: Path) -> None:
    source, destination = source.absolute(), destination.absolute()
    if _is_link(source) or not (source / "study_agent.db").is_file():
        raise DesktopDataError("请选择包含 study_agent.db 的旧版 data 目录。")
    if _is_link(source / "study_agent.db"):
        raise DesktopDataError("数据库不能使用链接文件。")
    if destination.exists():
        raise DesktopDataError("目标数据目录已存在，不能覆盖或合并已有数据。")
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise DesktopDataError("新旧数据目录不能互相包含。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".study-agent-import-", dir=destination.parent) as temp:
        staged = Path(temp) / "data"
        staged.mkdir()
        release._sqlite_backup(str(source / "study_agent.db"), str(staged / "study_agent.db"))
        _copy_configuration(source, staged)
        workspaces = source / "agent_workspaces"
        if workspaces.exists():
            _safe_tree(workspaces, staged / "agent_workspaces")
        prepare_database(staged / "study_agent.db")
        # 同一磁盘目录改名，避免半个导入结果被下一次启动使用。
        if destination.exists():
            raise DesktopDataError("目标数据目录已被创建，导入已停止。")
        os.rename(staged, destination)


def upgrade_database(path: Path) -> Path:
    path = path.absolute()
    before = _validated_database(path)
    backup_dir = path.parent / "backups" / uuid.uuid4().hex
    backup = Path(release.backup_database(str(path), str(backup_dir)))
    (backup_dir / "before.json").write_text(release.dumps(before), encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix=".study-agent-upgrade-", dir=path.parent) as temp:
        staged = Path(temp) / path.name
        release._sqlite_backup(str(backup), str(staged))
        prepare_database(staged)
        if _validated_database(path) != before:
            raise DesktopDataError("升级期间数据发生变化，请关闭其他实例后重试。")
        # 校验成功后清空旧 WAL；连接必须全部关闭才能替换 Windows 文件。
        conn = sqlite3.connect(path, timeout=5)
        try:
            mode = conn.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
            if mode.lower() != "delete":
                raise DesktopDataError("数据库仍被占用，请关闭其他实例后重试。")
        finally:
            conn.close()
        if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm")):
            raise DesktopDataError("数据库仍有活动 WAL 文件，请关闭其他实例后重试。")
        os.replace(staged, path)
    return backup
