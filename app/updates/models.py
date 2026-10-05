"""更新服务与 UI 之间的精简、不可变契约。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re

REPOSITORY = "planet798/Study-agent"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"


class UpdateError(Exception):
    """仅包含可以展示给用户的受控信息。"""


class UpdateCancelled(UpdateError):
    pass


class UpdateState(str, Enum):
    IDLE = "idle"
    CHECKING = "checking"
    CURRENT = "current"
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    READY = "ready"
    PREPARING = "preparing"
    FAILED = "failed"


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value) or len(value) > 32:
        raise UpdateError("版本信息异常，该版本暂不可用于应用内更新。")
    return tuple(int(part) for part in value.split("."))


def digest(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", value):
        raise UpdateError("发布文件的校验信息异常。")
    return value[7:].lower()


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    release_id: int
    asset_id: int
    published_at: str
    notes: str
    url: str
    size: int
    sha256: str

    @property
    def filename(self) -> str:
        return f"StudyAgent-Setup-{self.version}-x64.exe"

    @property
    def cache_key(self) -> str:
        return f"{self.version}-{self.release_id}-{self.asset_id}"


@dataclass(frozen=True)
class DownloadedUpdate:
    release: ReleaseInfo
    installer: Path
