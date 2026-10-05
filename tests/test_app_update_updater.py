import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.updates import jobs, updater
from app.updates.jobs import validate_job, write_record
from app.updates.models import UpdateError


@pytest.fixture
def upgrade_job(tmp_path, monkeypatch):
    root = tmp_path / "用户目录"
    folder = root / "updates/job-test"
    folder.mkdir(parents=True)
    cache = root / "updates/0.2.3-3-4"
    cache.mkdir()
    installer = cache / "StudyAgent-Setup-0.2.3-x64.exe"
    installer.write_bytes(b"installer")
    install = tmp_path / "安装目录 有空格"
    install.mkdir()
    (install / "StudyAgent.exe").write_bytes(b"exe")
    job = folder / "job.json"
    write_record(job, {"version": "0.2.3", "sha256": hashlib.sha256(b"installer").hexdigest(),
                       "size": 9, "installer": str(installer), "install_dir": str(install), "pids": [123]})
    monkeypatch.setattr(jobs, "user_root", lambda: root)
    return job


class FakeLock:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


def execute(job, *, code=0, waiter_error=False, startup_status="started"):
    calls = []
    class Waiter:
        def __init__(self, pids):
            calls.append(("handles", pids))
        def wait(self, timeout):
            calls.append(("wait", timeout))
            assert (job.parent / "ready.json").exists()
            if waiter_error:
                raise UpdateError("应用尚未退出")
        def close(self):
            calls.append(("close",))
    def run(args, **kwargs):
        calls.append(("install", args))
        return SimpleNamespace(returncode=code)
    def launch(args, **kwargs):
        calls.append(("launch", args))
        write_record(job.parent / "result.json", {"status": startup_status, "version": "0.2.3"})
        return SimpleNamespace(pid=100, poll=lambda: 1 if startup_status != "started" else None)
    write_record(job.parent / "commit.json", {"commit": True})
    rc = updater.run_job(job, lock_factory=FakeLock, waiter_factory=Waiter,
                         run=run, launch=launch, tell=lambda msg: calls.append(("notify", msg)))
    return rc, calls


def test_waits_before_install_and_restarts_exactly_once(upgrade_job):
    rc, calls = execute(upgrade_job)
    assert rc == 0
    names = [c[0] for c in calls]
    assert names.index("wait") < names.index("install") < names.index("launch")
    assert names.count("launch") == 1
    args = next(c[1] for c in calls if c[0] == "install")
    assert "/SILENT" in args and "/NORESTART" in args and "/NOCLOSEAPPLICATIONS" in args
    assert any("安装目录 有空格" in arg for arg in args)


@pytest.mark.parametrize("code", [2, 5, 7, 8, 999])
def test_install_failure_never_launches_partially_updated_program(upgrade_job, code):
    rc, calls = execute(upgrade_job, code=code)
    assert rc == 1
    assert not any(c[0] == "launch" for c in calls)
    failure = json.loads((upgrade_job.parent / "result.json").read_text())
    assert failure["status"] == "failed" and failure["stage"] == "install"
    assert failure["version"] == "0.2.3" and failure["error_type"] == "UpdateError"
    assert Path(validate_job(upgrade_job)["installer"]).exists()


def test_restart_required_does_not_restart_machine_or_app(upgrade_job):
    rc, calls = execute(upgrade_job, code=3010)
    assert rc == 3010
    assert not any(c[0] == "launch" for c in calls)
    assert "手动重启" in next(c[1] for c in calls if c[0] == "notify")


def test_process_wait_timeout_does_not_install(upgrade_job):
    rc, calls = execute(upgrade_job, waiter_error=True)
    assert rc == 1
    assert not any(c[0] == "install" for c in calls)


def test_installed_but_failed_startup_is_not_success(upgrade_job):
    rc, calls = execute(upgrade_job, startup_status="failed")
    assert rc == 1
    assert json.loads((upgrade_job.parent / "result.json").read_text())["status"] == "failed"


def test_changed_checksum_stops_before_readiness(upgrade_job):
    job = validate_job(upgrade_job)
    Path(job["installer"]).write_bytes(b"corrupted")
    rc, calls = execute(upgrade_job)
    assert rc == 1
    assert not (upgrade_job.parent / "ready.json").exists()
    assert not any(c[0] == "install" for c in calls)


def test_abort_before_commit_does_not_install(upgrade_job):
    calls = []
    class Waiter:
        def __init__(self, pids):
            pass
        def close(self):
            calls.append("close")
    write_record(upgrade_job.parent / "abort.json", {"abort": True})
    assert updater.run_job(upgrade_job, lock_factory=FakeLock, waiter_factory=Waiter,
                           run=lambda *a, **kw: pytest.fail("must not install"), tell=lambda _: None) == 2
    assert calls == ["close"]


@pytest.mark.parametrize("change", [
    lambda j: j.update(version="evil"), lambda j: j.update(pids=[True]),
    lambda j: j.update(installer="/tmp/evil.exe"), lambda j: j.update(sha256="invalid"),
    lambda j: j.update(size=0),
])
def test_invalid_job_rejected(upgrade_job, change):
    job = validate_job(upgrade_job)
    change(job)
    write_record(upgrade_job, job)
    with pytest.raises(UpdateError):
        validate_job(upgrade_job)


def test_prepare_job_copies_helper_outside_installation(upgrade_job, monkeypatch):
    from app.updates.models import DownloadedUpdate, ReleaseInfo
    existing = validate_job(upgrade_job)
    install = Path(existing["install_dir"])
    (install / "StudyAgentUpdater.exe").write_bytes(b"helper")
    monkeypatch.setattr(jobs, "supported", lambda: True)
    monkeypatch.setattr(jobs, "exit_process_ids", lambda: [123])
    release = ReleaseInfo("0.2.3", 3, 4, "", "", "", 9, existing["sha256"])
    new_job = jobs.prepare_job(DownloadedUpdate(release, Path(existing["installer"])), install_dir=install)
    assert new_job.parent != install
    assert (new_job.parent / "StudyAgentUpdater.exe").read_bytes() == b"helper"
    assert validate_job(new_job)["install_dir"] == str(install)


def test_prepare_job_stops_on_insufficient_install_space(upgrade_job, monkeypatch):
    from app.updates.models import DownloadedUpdate, ReleaseInfo
    existing = validate_job(upgrade_job)
    install = Path(existing["install_dir"])
    (install / "StudyAgentUpdater.exe").write_bytes(b"helper")
    monkeypatch.setattr(jobs, "supported", lambda: True)
    monkeypatch.setattr(jobs.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    release = ReleaseInfo("0.2.3", 3, 4, "", "", "", 9, existing["sha256"])
    with pytest.raises(UpdateError, match="空间不足"):
        jobs.prepare_job(DownloadedUpdate(release, Path(existing["installer"])), install_dir=install)


def test_atomic_record_rewrite_does_not_depend_on_old_temporary_file(tmp_path):
    record = tmp_path / "record.json"
    record.with_suffix(".json.tmp").write_text("abandoned")
    write_record(record, {"state": 1})
    write_record(record, {"state": 2})
    assert json.loads(record.read_text()) == {"state": 2}
