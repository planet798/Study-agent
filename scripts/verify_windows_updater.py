"""CI 隔离升级实测：旧正式安装包 → 本次构建，保留任务/消息/文件。

只在 Windows CI 的临时 LOCALAPPDATA 中使用合成数据；不用于用户正式库。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.app_update_service import AppUpdateService
from app.updates.jobs import read_record, write_record
from app.updates.models import LATEST_URL, REPOSITORY, version_tuple
from app.updates.network import GitHubTransport


def wait_for(predicate, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise RuntimeError("Windows updater verification timed out")


def checked_installer(path, install):
    result = subprocess.run([str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                             "/NOCLOSEAPPLICATIONS", f"/DIR={install}"], check=False)
    if result.returncode != 0:
        raise RuntimeError("Baseline installation failed")


def snapshot(db, task_id, session_id):
    with sqlite3.connect(db) as conn:
        return {
            "task": conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone(),
            "session": conn.execute("SELECT * FROM agent_sessions WHERE id=?", (session_id,)).fetchone(),
            "messages": conn.execute("SELECT * FROM agent_messages WHERE session_id=? ORDER BY id", (session_id,)).fetchall(),
        }


def verify(installer, helper, install, version, report):
    local = Path(os.environ["LOCALAPPDATA"]).resolve()
    if "RUNNER_TEMP" not in os.environ or not local.is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve()):
        raise RuntimeError("Verification requires isolated RUNNER_TEMP/LOCALAPPDATA")
    root = local / "StudyAgent"
    updates = root / "updates"
    # CI 仅用自己的只读 token 获取公开基线元数据，避免共享出口的匿名 API 限流。
    # 应用及辅助程序不读取此 token；附件下载仍复用生产 HTTPS/摘要校验。
    metadata = subprocess.run(["gh", "api", f"repos/{REPOSITORY}/releases/latest"],
                              check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
    class BaselineTransport(GitHubTransport):
        def open(self, url, timeout):
            return io.BytesIO(metadata) if url == LATEST_URL else super().open(url, timeout)
    service = AppUpdateService(current_version="0.0.0", cache_root=updates, transport=BaselineTransport())
    baseline = service.check()
    baseline_version = version
    if baseline is not None and version_tuple(baseline.version) < version_tuple(version):
        baseline_package = service.download(baseline)
        checked_installer(baseline_package.installer, install)
        baseline_version = baseline.version
    else:
        checked_installer(installer, install)

    from app.database.connection import get_fresh_connection
    from app.database.repository import TaskRepository
    from app.database.agent_repository import AgentRepository
    db = root / "data/study_agent.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = get_fresh_connection(db)
    tasks = TaskRepository(conn)
    task = tasks.create("安装升级测试任务", task_type="manual")
    tasks.mark_done(task.id)
    agents = AgentRepository(conn)
    session = agents.create_session(task.id, "安装升级测试会话")
    agents.add_message(session["id"], "user", "合成历史消息")
    agents.add_message(session["id"], "assistant", "合成历史回答")
    conn.close()
    before = snapshot(db, task.id, session["id"])
    note = root / "data/workspaces/update-probe/note.md"
    note.parent.mkdir(parents=True)
    note.write_text("合成学习文件", encoding="utf-8")
    config = root / "data/update-probe.json"
    config.write_text('{"synthetic": true}', encoding="utf-8")

    folder = updates / ("job-" + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    cache = updates / ("test-new-" + uuid.uuid4().hex)
    cache.mkdir()
    package = cache / installer.name
    shutil.copy2(installer, package)
    executable = folder / "StudyAgentUpdater.exe"
    shutil.copy2(helper, executable)
    wait_process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    job = folder / "job.json"
    write_record(job, {"version": version, "installer": str(package), "install_dir": str(install),
                       "size": package.stat().st_size, "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                       "pids": [wait_process.pid]})
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    updater = subprocess.Popen([str(executable), str(job)], cwd=folder, env=environment)
    app_pid = None
    try:
        wait_for(lambda: (folder / "ready.json").exists())
        write_record(folder / "commit.json", {"commit": True})
        time.sleep(0.3)
        if (folder / "installer.log").exists() or (folder / "result.json").exists():
            raise RuntimeError("Updater did not wait for the live process")
        wait_process.terminate()
        wait_process.wait(timeout=5)
        def started():
            result = folder / "result.json"
            if result.exists():
                status = read_record(result).get("status")
                if status == "failed":
                    raise RuntimeError("Packaged updater failed; inspect updater logs")
                return status == "started"
            return False
        wait_for(started, 180)
        app_pid = read_record(folder / "launched.json")["pid"]
        if updater.wait(timeout=10) != 0:
            raise RuntimeError("Packaged updater did not complete successfully")
        if snapshot(db, task.id, session["id"]) != before:
            raise RuntimeError("Update changed historical tasks or messages")
        if note.read_text(encoding="utf-8") != "合成学习文件" or config.read_text() != '{"synthetic": true}':
            raise RuntimeError("Update changed user files or configuration")
        report.write_text(json.dumps({"ok": True, "baseline": baseline_version, "target": version,
                                     "waited_for_exit": True, "history_preserved": True,
                                     "files_preserved": True, "automatic_restart": True}), encoding="utf-8")
    finally:
        launched = folder / "launched.json"
        if app_pid is None and launched.exists():
            app_pid = read_record(launched).get("pid")
        if app_pid:
            # 只结束本次隔离测试创建的进程树，不搜索或关闭其他用户应用。
            subprocess.run(["taskkill", "/PID", str(app_pid), "/T", "/F"], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for process in (wait_process, updater):
            if process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                process.wait(timeout=10)
        for filename in ("result.json", "installer.log"):
            source = folder / filename
            if source.exists():
                shutil.copy2(source, report.parent / ("updater-" + filename))


def main():
    parser = argparse.ArgumentParser()
    for name in ("installer", "helper", "install", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    verify(args.installer.resolve(), args.helper.resolve(), args.install.resolve(), args.version, args.report.resolve())


if __name__ == "__main__":
    main()
