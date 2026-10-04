"""资源只读、用户数据可写；源码运行保持历史目录布局。"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def user_root() -> Path:
    if not is_frozen():
        return resource_root()
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("无法确定 Windows 用户数据目录。")
    return Path(local) / "StudyAgent"


def data_dir() -> Path:
    return user_root() / "data"


def logs_dir() -> Path:
    return user_root() / ("logs" if is_frozen() else "data/logs")


def context_path() -> Path:
    return data_dir() / "career_context.json" if is_frozen() else resource_root() / "docs/career_context.json"


def notes_dir() -> Path:
    return user_root() / "notes" if is_frozen() else resource_root() / "docs/obsidian"


def bridge_dir() -> Path:
    return resource_root() / "oauth_bridge"


def node_executable() -> str:
    if is_frozen():
        # 发布版始终调用随包的运行时，不受 PATH / 开发环境变量影响。
        return str(resource_root() / "runtime/node.exe")
    return os.environ.get("STUDY_AGENT_NODE") or "node"


def hidden_process_options() -> dict:
    return {"creationflags": 0x08000000} if os.name == "nt" else {}
