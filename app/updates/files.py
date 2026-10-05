"""更新文件的链接边界，包含 Windows 联接点。"""
from pathlib import Path


def is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
