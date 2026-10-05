"""Windows API 集成；Linux 明确跳过，Windows CI 使用真实进程句柄。"""
import os
import subprocess
import sys

import pytest

from app.updates.models import UpdateError
from app.updates.windows import ProcessWaiter, UpdateLock, update_running

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires Windows process handles")


def test_cross_process_mutex_blocks_second_update(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert not update_running()
    with UpdateLock():
        assert update_running()
        with pytest.raises(UpdateError, match="另一个"):
            with UpdateLock():
                pass
        result = subprocess.run([sys.executable, "-c", "from app.updates.windows import update_running; raise SystemExit(0 if update_running() else 1)"], check=False)
        assert result.returncode == 0
    assert not update_running()


def test_waiter_does_not_kill_live_process():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    waiter = ProcessWaiter([process.pid])
    try:
        with pytest.raises(UpdateError, match="尚未"):
            waiter.wait(10)
        assert process.poll() is None
        process.terminate()
        process.wait(timeout=5)
        waiter.wait(1000)
    finally:
        waiter.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_windows_junction_is_a_rejected_cache_boundary(tmp_path):
    from app.updates.files import is_link
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "junction"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert is_link(link)
    link.rmdir()
    assert target.is_dir()
