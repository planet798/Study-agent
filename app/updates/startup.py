"""启动升级锁、版本确认和缓存清理；数据库迁移仍走既有入口。"""
from __future__ import annotations

from pathlib import Path
import shutil
import time

from app.updates.files import is_link
from app.runtime_paths import user_root
from app.version import VERSION
from .jobs import read_record, supported, validate_job, write_record
from .models import UpdateError
from .windows import notify, update_running

_pending: Path | None = None


def initialize(argv: list[str]) -> bool:
    global _pending
    _pending = None
    if not supported():
        return True
    if update_running():
        notify("Study Agent 正在更新，请等待安装完成后再启动。")
        return False
    if "--update-result" in argv:
        index = argv.index("--update-result")
        if index + 1 >= len(argv):
            raise UpdateError("升级结果参数不完整。")
        path = Path(argv[index + 1])
        validate_job(path)
        result = read_record(path.parent / "result.json")
        if result.get("status") != "installed" or result.get("version") != VERSION:
            raise UpdateError("实际启动版本与升级目标不一致，请修复安装。")
        _pending = path
        del argv[index:index + 2]
    try:
        cleanup_cache(_pending)
    except OSError:
        pass  # 缓存清理失败不阻止正常使用应用。
    return True


def confirm_started(window):
    if _pending is None:
        return
    try:
        # 启动期间任务文件可能变化，清理前再次验证缓存目录边界。
        job = validate_job(_pending)
    except (OSError, ValueError, KeyError, UpdateError):
        fail_startup()
        window.statusBar().showMessage("无法验证更新结果，安装包和学习数据保留。", 15000)
        return
    if VERSION != job["version"]:
        fail_startup()
        return
    write_record(_pending.parent / "result.json", {"status": "started", "version": VERSION})
    window.statusBar().showMessage(f"已更新至 Study Agent {VERSION}。", 15000)
    panel = window.ai_settings_page.update_panel
    panel.controller.message = f"已成功更新至 {VERSION}。"
    panel.controller.changed.emit()
    # 保留结果供仍在等待确认的辅助程序读取，只清理已用完的安装包。
    installer = Path(job["installer"])
    try:
        installer.unlink(missing_ok=True)
        installer.parent.rmdir()
    except OSError:
        pass


def fail_startup():
    if _pending is not None:
        try:
            write_record(_pending.parent / "result.json", {
                "status": "failed", "version": VERSION,
                "error": "新版启动或数据准备未完成，学习数据保留。",
            })
        except OSError:
            pass


def cleanup_cache(active=None):
    """失败保留最近一次；只移除受控目录，跳过链接和近期运行任务。"""
    root = user_root() / "updates"
    if not root.exists() or is_link(root):
        return
    now = time.time()
    failed = []
    protected = set()
    removable = []
    for folder in root.iterdir():
        if not folder.is_dir() or is_link(folder):
            continue
        if folder.name.startswith("job-"):
            try:
                job = read_record(folder / "job.json")
                result_file = folder / "result.json"
                result = read_record(result_file) if result_file.exists() else {}
                age = now - folder.stat().st_mtime
                if active is not None and folder == Path(active).parent:
                    protected.add(Path(job["installer"]).parent)
                elif result.get("status") == "failed":
                    failed.append((folder.stat().st_mtime, folder, job))
                elif result.get("status") == "started" and age > 60:
                    removable.append(folder)
                elif age > 86400 and result.get("status") in (None, "installed"):
                    removable.append(folder)
                else:
                    protected.add(Path(job["installer"]).parent)
            except (OSError, ValueError, KeyError, UpdateError):
                continue
    for index, (_, folder, job) in enumerate(sorted(failed, reverse=True)):
        if index == 0 or now - folder.stat().st_mtime < 60:
            protected.add(Path(job["installer"]).parent)
        else:
            removable.append(folder)
    for folder in root.iterdir():
        if (folder.is_dir() and not is_link(folder) and not folder.name.startswith("job-")
                and folder not in protected and now - folder.stat().st_mtime > 7 * 86400):
            removable.append(folder)
    for folder in removable:
        shutil.rmtree(folder, ignore_errors=True)
