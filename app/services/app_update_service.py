"""固定正式 Release 的检查、下载和 SHA256 校验；可注入离线传输测试。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil

from app.updates.files import is_link
from app.runtime_paths import user_root
from app.updates.models import (
    DownloadedUpdate, LATEST_URL, REPOSITORY, ReleaseInfo,
    UpdateCancelled, UpdateError, digest, version_tuple,
)
from app.updates.network import GitHubTransport, read_chunks
from app.version import VERSION


class AppUpdateService:
    def __init__(self, *, current_version=VERSION, cache_root=None, transport=None):
        self.current_version = current_version
        self.cache_root = Path(cache_root) if cache_root is not None else user_root() / "updates"
        self.transport = transport or GitHubTransport()

    def _read(self, url, limit, cancel):
        with self.transport.open(url, 15) as response:
            return b"".join(read_chunks(response, cancel, limit=limit))

    def check(self, cancel=lambda: False) -> ReleaseInfo | None:
        try:
            release = json.loads(self._read(LATEST_URL, 1_048_576, cancel))
            if not isinstance(release, dict):
                raise ValueError
            if release.get("draft") is not False or release.get("prerelease") is not False:
                return None
            tag = release["tag_name"]
            if not isinstance(tag, str) or not tag.startswith("v"):
                raise ValueError
            version = tag[1:]
            if version_tuple(version) <= version_tuple(self.current_version):
                return None
            filename = f"StudyAgent-Setup-{version}-x64.exe"
            assets = release["assets"]
            installer = self._asset(assets, filename, version)
            checksum = self._asset(assets, "SHA256SUMS.txt", version)
            body = self._read(checksum["browser_download_url"], 65_536, cancel)
            if len(body) != checksum["size"]:
                raise UpdateError("校验文件下载不完整，请重新检查更新。")
            api_checksum = digest(checksum.get("digest"))
            if api_checksum and hashlib.sha256(body).hexdigest() != api_checksum:
                raise UpdateError("发布校验文件摘要不一致，已停止更新。")
            matches = re.findall(r"^([0-9a-fA-F]{64})[ \t]+\*?" + re.escape(filename) + r"[ \t]*\r?$",
                                 body.decode("utf-8-sig"), re.MULTILINE)
            if len(matches) != 1:
                raise UpdateError("发布校验文件缺少唯一匹配的安装包信息。")
            sha256 = matches[0].lower()
            api_digest = digest(installer.get("digest"))
            if api_digest and sha256 != api_digest:
                raise UpdateError("安装包校验信息不一致，已停止更新。")
            release_id = release["id"]
            if type(release_id) is not int or release_id <= 0:
                raise ValueError
            return ReleaseInfo(version, release_id, installer["id"],
                               str(release.get("published_at", ""))[:64],
                               str(release.get("body") or "")[:32_000],
                               installer["browser_download_url"], installer["size"], sha256)
        except (KeyError, TypeError, ValueError, UnicodeError):
            raise UpdateError("发布信息异常，该版本暂不可用于应用内更新。") from None

    @staticmethod
    def _asset(assets, name, version):
        if not isinstance(assets, list):
            raise ValueError
        matches = [a for a in assets if isinstance(a, dict) and a.get("name") == name]
        if len(matches) != 1:
            raise UpdateError("正式版本缺少安装包或校验附件，暂不可更新。")
        asset = matches[0]
        expected = f"https://github.com/{REPOSITORY}/releases/download/v{version}/{name}"
        if (asset.get("browser_download_url") != expected
                or asset.get("state") != "uploaded"
                or type(asset.get("id")) is not int or asset["id"] <= 0
                or type(asset.get("size")) is not int or asset["size"] <= 0):
            raise UpdateError("发布附件信息异常，暂不可更新。")
        return asset

    def download(self, release: ReleaseInfo, *, cancel=lambda: False,
                 progress=lambda current, total: None, verifying=lambda: None) -> DownloadedUpdate:
        try:
            self.cache_root.mkdir(parents=True, exist_ok=True)
            if is_link(self.cache_root):
                raise UpdateError("更新缓存目录不能是链接。")
            folder = self.cache_root / release.cache_key
            folder.mkdir(exist_ok=True)
            if is_link(folder):
                raise UpdateError("更新缓存目录不能是链接。")
            final = folder / release.filename
            if final.exists():
                verifying()
                if self.verify(final, release, cancel):
                    return DownloadedUpdate(release, final)
                final.unlink()
            if shutil.disk_usage(folder).free < release.size + 16 * 1024 * 1024:
                raise UpdateError("更新缓存磁盘空间不足，请清理后重新下载。")
            partial = folder / (release.filename + ".part")
            partial.unlink(missing_ok=True)
            try:
                done = 0
                with self.transport.open(release.url, 30) as response, partial.open("xb") as output:
                    for block in read_chunks(response, cancel, limit=release.size):
                        output.write(block)
                        done += len(block)
                        progress(done, release.size)
                verifying()
                if not self.verify(partial, release, cancel):
                    raise UpdateError("安装包下载不完整或 SHA256 不匹配，请重新下载。")
                partial.replace(final)
                return DownloadedUpdate(release, final)
            finally:
                partial.unlink(missing_ok=True)
        except OSError:
            raise UpdateError("无法写入更新缓存，请检查磁盘空间和目录权限。") from None

    @staticmethod
    def verify(path: Path, release: ReleaseInfo, cancel=lambda: False) -> bool:
        if is_link(path) or not path.is_file() or path.stat().st_size != release.size:
            return False
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(64 * 1024), b""):
                if cancel():
                    raise UpdateCancelled("下载已取消。")
                checksum.update(block)
        if cancel():
            raise UpdateCancelled("下载已取消。")
        return checksum.hexdigest() == release.sha256
