"""跨进程升级任务与结果；原子写入，不包含学习内容或凭据。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

from app.updates.files import is_link
from app.runtime_paths import is_frozen, user_root
from .models import DownloadedUpdate, UpdateError, version_tuple
from .windows import exit_process_ids


def write_record(path: Path, value: dict):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_record(path: Path) -> dict:
    if is_link(path) or path.stat().st_size > 16_384:
        raise UpdateError("升级任务文件异常。")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise UpdateError("升级任务文件异常。")
    return value


def supported() -> bool:
    return os.name == "nt" and is_frozen()


def prepare_job(download: DownloadedUpdate, *, install_dir: Path | None = None) -> Path:
    if not supported():
        raise UpdateError("源码运行不支持自动安装，请打开发布页下载安装版。")
    install = install_dir or Path(sys.executable).resolve().parent
    helper = install / "StudyAgentUpdater.exe"
    if not helper.is_file():
        raise UpdateError("升级辅助程序缺失，请从发布页修复安装。")
    # 安装包压缩后大小不能代表解压所需空间；至少预留现有程序大小及额外余量。
    installed_size = 0
    for current, directories, files in os.walk(install, followlinks=False):
        directories[:] = [name for name in directories if not is_link(Path(current) / name)]
        for name in files:
            file = Path(current) / name
            if not is_link(file):
                installed_size += file.stat().st_size
    required = max(download.release.size * 4, installed_size * 2) + 64 * 1024 * 1024
    if shutil.disk_usage(install).free < required:
        raise UpdateError("安装磁盘空间不足，请清理后重试。")
    try:
        probe = install / (".update-write-test-" + uuid.uuid4().hex)
        with probe.open("xb"):
            pass
        probe.unlink()
    except OSError:
        raise UpdateError("安装目录不可写，请检查权限或手动运行安装包。") from None
    directory = user_root() / "updates" / ("job-" + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    shutil.copy2(helper, directory / "StudyAgentUpdater.exe")
    job = directory / "job.json"
    write_record(job, {
        "version": download.release.version, "sha256": download.release.sha256,
        "size": download.release.size, "installer": str(download.installer.resolve()),
        "install_dir": str(install.resolve()), "pids": exit_process_ids(),
    })
    return job


def validate_job(path: Path) -> dict:
    path = Path(path)
    root = user_root() / "updates"
    if path.name != "job.json" or path.parent.parent.resolve() != root.resolve():
        raise UpdateError("升级任务不在指定缓存目录。")
    if is_link(path.parent) or is_link(root):
        raise UpdateError("升级目录不能是链接。")
    job = read_record(path)
    version_tuple(job["version"])
    if not re.fullmatch(r"[0-9a-f]{64}", job["sha256"]):
        raise UpdateError("安装包校验信息异常。")
    if type(job["size"]) is not int or job["size"] <= 0:
        raise UpdateError("安装包大小异常。")
    pids = job["pids"]
    if (not isinstance(pids, list) or not 1 <= len(pids) <= 2
            or any(type(pid) is not int or pid <= 0 for pid in pids)):
        raise UpdateError("待退出进程信息异常。")
    installer = Path(job["installer"])
    if (not installer.is_absolute() or is_link(installer)
            or installer.name != f"StudyAgent-Setup-{job['version']}-x64.exe"
            or installer.parent.parent.resolve() != root.resolve()
            or is_link(installer.parent)):
        raise UpdateError("安装包不在指定缓存目录。")
    install = Path(job["install_dir"])
    if not install.is_absolute() or not (install / "StudyAgent.exe").is_file():
        raise UpdateError("原安装目录不可用。")
    return job


def verify_installer(job: dict):
    path = Path(job["installer"])
    if path.stat().st_size != job["size"]:
        raise UpdateError("安装包大小已变化，升级已停止。")
    sha256 = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha256.update(block)
    if sha256.hexdigest() != job["sha256"]:
        raise UpdateError("安装包 SHA256 不匹配，升级已停止。")
