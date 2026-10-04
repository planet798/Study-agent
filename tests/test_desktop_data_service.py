"""真实 SQLite/WAL、失败保护与跨目录资源导入回归。"""
import sqlite3
from pathlib import Path

import pytest

from app.database.connection import get_connection
from app.database.repository import TaskRepository
from app.database.schema import SCHEMA_VERSION
from app.services.task_service import TaskService
from app.services import desktop_data_service as service


@pytest.fixture
def legacy(tmp_path):
    data = tmp_path / "旧 项目/data"
    data.mkdir(parents=True)
    conn = get_connection(data / "study_agent.db")
    TaskService(TaskRepository(conn)).create_task("保留学习记录")
    yield data, conn
    conn.close()


def test_import_includes_committed_wal_workspaces_and_configuration(legacy, tmp_path):
    source, conn = legacy
    workspace = source / "agent_workspaces/task_1"
    workspace.mkdir(parents=True)
    (workspace / "笔记.md").write_text("学习笔记", encoding="utf-8")
    (source / "mcp_servers.json").write_text('{"version":1,"servers":[]}', encoding="utf-8")
    (source / "sandbox.json").write_text('{"version":1,"enabled":false}', encoding="utf-8")
    (source / "logs").mkdir()
    (source / "logs/private.log").write_text("must not copy")
    destination = tmp_path / "新 用户/data"
    service.import_legacy_data(source, destination)
    with sqlite3.connect(destination / "study_agent.db") as imported:
        assert imported.execute("SELECT title FROM tasks").fetchone()[0] == "保留学习记录"
    assert (destination / "agent_workspaces/task_1/笔记.md").read_text(encoding="utf-8") == "学习笔记"
    assert (destination / "mcp_servers.json").exists()
    assert not (destination / "logs").exists()
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_import_never_overwrites_existing_destination(legacy, tmp_path):
    destination = tmp_path / "existing"
    destination.mkdir()
    sentinel = destination / "study_agent.db"
    sentinel.write_bytes(b"existing database")
    with pytest.raises(service.DesktopDataError, match="不能覆盖"):
        service.import_legacy_data(legacy[0], destination)
    assert sentinel.read_bytes() == b"existing database"


def test_failed_validation_does_not_publish_partial_import(legacy, tmp_path, monkeypatch):
    destination = tmp_path / "new/data"
    def fail(path):
        raise service.DesktopDataError("校验失败")
    monkeypatch.setattr(service, "prepare_database", fail)
    with pytest.raises(service.DesktopDataError):
        service.import_legacy_data(legacy[0], destination)
    assert not destination.exists()
    assert not list(destination.parent.glob(".study-agent-import-*"))


def test_newer_database_is_rejected_before_mutation(legacy, tmp_path):
    source, conn = legacy
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    conn.commit()
    destination = tmp_path / "new/data"
    with pytest.raises(service.DesktopDataError, match="较新"):
        service.import_legacy_data(source, destination)
    assert not destination.exists()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION + 1


def test_workspace_links_are_rejected(legacy, tmp_path):
    source, _ = legacy
    workspace = source / "agent_workspaces"
    workspace.mkdir()
    try:
        (workspace / "escape").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(service.DesktopDataError, match="链接"):
        service.import_legacy_data(source, tmp_path / "new/data")


def test_invalid_configuration_never_reveals_values(legacy, tmp_path):
    (legacy[0] / "mcp_servers.json").write_text('{"secret":"do-not-expose"}', encoding="utf-8")
    with pytest.raises(service.DesktopDataError) as error:
        service.import_legacy_data(legacy[0], tmp_path / "new/data")
    assert "do-not-expose" not in str(error.value)


def test_upgrade_failure_preserves_original_and_consistent_backup(legacy, monkeypatch):
    source, conn = legacy
    conn.close()
    path = source / "study_agent.db"
    original = path.read_bytes()
    def fail(path):
        raise service.DesktopDataError("预演失败")
    monkeypatch.setattr(service, "prepare_database", fail)
    with pytest.raises(service.DesktopDataError):
        service.upgrade_database(path)
    assert path.read_bytes() == original
    backup = next((source / "backups").glob("*/*.db"))
    with sqlite3.connect(backup) as saved:
        assert saved.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_upgrade_success_promotes_verified_database_and_keeps_backup(legacy):
    source, conn = legacy
    conn.close()
    backup = service.upgrade_database(source / "study_agent.db")
    assert backup.exists()
    with sqlite3.connect(source / "study_agent.db") as upgraded:
        assert upgraded.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert upgraded.execute("SELECT title FROM tasks").fetchone()[0] == "保留学习记录"


def test_gate_rejects_future_schema(legacy):
    from app.main import migration_gate_status
    source, conn = legacy
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    conn.commit()
    assert migration_gate_status(source / "study_agent.db")["reason"] == "newer_schema"


@pytest.mark.parametrize("operation", ["import", "upgrade"])
def test_real_v25_upgrade_preserves_session_history_and_local_binding(tmp_path, operation):
    from app.database.connection import get_raw_connection
    from app.database.schema import migrate_stepwise
    from app.database.agent_repository import AgentRepository
    from app.database.task_workspace_repository import TaskWorkspaceRepository

    source = tmp_path / "legacy/data"
    path = source / "study_agent.db"
    conn = get_raw_connection(path)
    migrate_stepwise(conn, target=25)
    task = TaskRepository(conn).create(title="原学习任务", scheduled_date="2026-01-01")
    agent = AgentRepository(conn)
    session = agent.create_session(task.id, "Original snapshot")
    agent.add_message(session["id"], "user", "原问题")
    agent.add_message(session["id"], "assistant", "原答案")
    TaskWorkspaceRepository(conn).upsert(task.id, "local", str(tmp_path / "external project"))
    original = agent.list_messages(session["id"])
    conn.close()
    if operation == "import":
        target = tmp_path / "installed/data"
        service.import_legacy_data(source, target)
        with sqlite3.connect(path) as untouched:
            assert untouched.execute("PRAGMA user_version").fetchone()[0] == 25
        path = target / "study_agent.db"
    else:
        service.upgrade_database(path)
    conn = get_raw_connection(path, read_only=True)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert AgentRepository(conn).list_messages(session["id"]) == original
        assert TaskWorkspaceRepository(conn).get_by_task(task.id)["local_path"] == str(tmp_path / "external project")
    finally:
        conn.close()
