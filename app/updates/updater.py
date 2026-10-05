"""独立冻结升级程序：先握手，等待退出，安装成功才启动新版。"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time

from app.updates.jobs import read_record, validate_job, verify_installer, write_record
from app.updates.models import UpdateError
from app.updates.windows import ProcessWaiter, UpdateLock, notify


class UpgradeAbort(UpdateError):
    """主程序撤回退出请求；不应运行安装器。"""


def installer_arguments(job, folder):
    return [job["installer"], "/SILENT", "/SUPPRESSMSGBOXES", "/SP-", "/NORESTART",
            "/RESTARTEXITCODE=3010", "/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS",
            f'/DIR={job["install_dir"]}', f'/LOG={folder / "installer.log"}']


def run_job(path: Path, *, lock_factory=UpdateLock, waiter_factory=ProcessWaiter,
            run=subprocess.run, launch=subprocess.Popen, tell=notify) -> int:
    path = Path(path)
    folder = path.parent
    waiter = None
    job = None
    stage = "prepare"
    try:
        job = validate_job(path)
        with lock_factory():
            waiter = waiter_factory(job["pids"])
            verify_installer(job)
            write_record(folder / "ready.json", {"ready": True})
            # 只有主程序明确承诺退出后才能安装；仅仅拿到 PID 不构成授权。
            deadline = time.monotonic() + 30
            while not (folder / "commit.json").exists():
                if (folder / "abort.json").exists() or time.monotonic() > deadline:
                    raise UpgradeAbort("升级准备已取消，应用保持运行。")
                time.sleep(0.1)
            if read_record(folder / "commit.json").get("commit") is not True:
                raise UpgradeAbort("升级准备已取消。")
            stage = "wait_exit"
            waiter.wait(60_000)
            waiter.close()
            waiter = None
            verify_installer(job)
            stage = "install"
            result = run(installer_arguments(job, folder), check=False)
            if result.returncode == 3010:
                write_record(folder / "result.json", {"status": "restart_required", "version": job["version"]})
                tell("安装完成，需要手动重启电脑后再启动 Study Agent。学习数据已保留。")
                return 3010
            if result.returncode != 0:
                raise UpdateError("安装未完成，请重新运行已下载的安装包修复安装。")
            write_record(folder / "result.json", {"status": "installed", "version": job["version"]})
        # 安装结束并释放升级锁后启动一次；安装器自身 skipifsilent 不启动应用。
        stage = "restart"
        process = launch([str(Path(job["install_dir"]) / "StudyAgent.exe"),
                          "--update-result", str(path)], cwd=job["install_dir"])
        write_record(folder / "launched.json", {"pid": process.pid})
        # 包括迁移确认在内最多观察十分钟，不把进程创建成功误报为启动成功。
        stage = "confirm_start"
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            record = read_record(folder / "result.json")
            if record.get("status") == "started":
                return 0
            if record.get("status") == "failed" or process.poll() is not None:
                break
            time.sleep(0.25)
        raise UpdateError("新版未确认启动成功。请保留学习数据，重新启动或运行安装包修复。")
    except UpgradeAbort:
        return 2
    except Exception as error:
        message = str(error) if isinstance(error, UpdateError) else "升级失败，请检查磁盘空间和权限，或重新运行已下载的安装包。"
        try:
            # 未验证任务也只能写入已存在的任务目录，不回显异常/临时下载地址。
            if job is not None:
                write_record(folder / "result.json", {"status": "failed", "error": message,
                                                       "version": job["version"], "stage": stage,
                                                       "error_type": type(error).__name__})
        except (OSError, ValueError):
            pass
        location = f"安装包：{job['installer']}\n日志目录：{folder}" if job else f"任务目录：{folder}"
        tell(f"{message}\n\n{location}\n学习数据保留。")
        return 1
    finally:
        if waiter is not None:
            waiter.close()


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    return run_job(Path(sys.argv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
