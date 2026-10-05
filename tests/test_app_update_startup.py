import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.updates import startup
from app.updates.jobs import write_record
from app.updates.models import UpdateError
from tests.test_app_update_updater import upgrade_job


@pytest.fixture(autouse=True)
def isolate_pending(monkeypatch):
    monkeypatch.setattr(startup, "_pending", None)


def test_upgrade_lock_blocks_startup_before_main(monkeypatch):
    monkeypatch.setattr(startup, "supported", lambda: True)
    monkeypatch.setattr(startup, "update_running", lambda: True)
    notices = []
    monkeypatch.setattr(startup, "notify", notices.append)
    assert startup.initialize(["StudyAgent.exe"]) is False
    assert notices


def test_actual_version_must_match_result(upgrade_job, monkeypatch):
    monkeypatch.setattr(startup, "supported", lambda: True)
    monkeypatch.setattr(startup, "update_running", lambda: False)
    write_record(upgrade_job.parent / "result.json", {"status": "installed", "version": "0.2.3"})
    monkeypatch.setattr(startup, "VERSION", "0.2.2")
    with pytest.raises(UpdateError, match="不一致"):
        startup.initialize(["StudyAgent.exe", "--update-result", str(upgrade_job)])


def test_startup_acknowledges_version_and_preserves_result_for_helper(upgrade_job, monkeypatch):
    monkeypatch.setattr(startup, "supported", lambda: True)
    monkeypatch.setattr(startup, "update_running", lambda: False)
    monkeypatch.setattr(startup, "VERSION", "0.2.3")
    monkeypatch.setattr(startup, "cleanup_cache", lambda _: None)
    write_record(upgrade_job.parent / "result.json", {"status": "installed", "version": "0.2.3"})
    args = ["StudyAgent.exe", "--update-result", str(upgrade_job)]
    assert startup.initialize(args)
    assert args == ["StudyAgent.exe"]
    messages = []
    controller = SimpleNamespace(message="", changed=SimpleNamespace(emit=lambda: None))
    window = SimpleNamespace(statusBar=lambda: SimpleNamespace(showMessage=lambda *a: messages.append(a)),
                             ai_settings_page=SimpleNamespace(update_panel=SimpleNamespace(controller=controller)))
    job = json.loads(upgrade_job.read_text(encoding="utf-8"))
    startup.confirm_started(window)
    assert json.loads((upgrade_job.parent / "result.json").read_text(encoding="utf-8"))["status"] == "started"
    assert not Path(job["installer"]).exists()
    assert messages
    monkeypatch.setattr(startup, "_pending", None)


def test_cache_failure_never_blocks_normal_startup(monkeypatch):
    monkeypatch.setattr(startup, "supported", lambda: True)
    monkeypatch.setattr(startup, "update_running", lambda: False)
    def fail(_):
        raise PermissionError
    monkeypatch.setattr(startup, "cleanup_cache", fail)
    assert startup.initialize(["StudyAgent.exe"])


def test_cache_cleanup_retains_latest_failure_and_skips_links(tmp_path, monkeypatch):
    monkeypatch.setattr(startup, "user_root", lambda: tmp_path)
    root = tmp_path / "updates"
    for index in (1, 2):
        folder = root / f"job-{index}"
        folder.mkdir(parents=True)
        write_record(folder / "job.json", {"installer": str(root / f"cache-{index}" / "installer.exe")})
        write_record(folder / "result.json", {"status": "failed"})
        os.utime(folder, (time.time() - index * 1000, time.time() - index * 1000))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("preserve")
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    startup.cleanup_cache()
    assert (root / "job-1").exists()
    assert not (root / "job-2").exists()
    assert (outside / "keep.txt").exists()


def test_changed_job_cannot_delete_file_outside_cache(upgrade_job, monkeypatch, tmp_path):
    outside = tmp_path / "learning-note.md"
    outside.write_text("preserve")
    record = json.loads(upgrade_job.read_text(encoding="utf-8"))
    record["installer"] = str(outside)
    write_record(upgrade_job, record)
    monkeypatch.setattr(startup, "_pending", upgrade_job)
    messages = []
    window = SimpleNamespace(statusBar=lambda: SimpleNamespace(showMessage=lambda *a: messages.append(a)))
    startup.confirm_started(window)
    assert outside.read_text(encoding="utf-8") == "preserve"
    assert json.loads((upgrade_job.parent / "result.json").read_text(encoding="utf-8"))["status"] == "failed"
    assert messages
