"""Task-scoped bounded filesystem workspace with traversal/link protection."""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path, PureWindowsPath
from typing import Any

from ..tools.base import AgentToolError

MAX_DIRECTORY_ENTRIES = 200
MAX_SANDBOX_PATH_CHARS = 1024
MAX_SANDBOX_PATH_COMPONENTS = 32
_WINDOWS_INVALID = set('<>:"|?*')
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                     *(f"LPT{i}" for i in range(1, 10))}


class SandboxWorkspaceError(AgentToolError):
    code = "sandbox_workspace_error"

    def __init__(self, message: str = "Sandbox workspace operation failed."):
        super().__init__(message)


class SandboxPathError(SandboxWorkspaceError):
    code = "sandbox_path_invalid"


class SandboxFileTooLargeError(SandboxWorkspaceError):
    code = "sandbox_file_too_large"


class SandboxWorkspace:
    """One lazy ``task_<integer>`` workspace beneath an operator-chosen root.

    The constructor performs no filesystem mutation. Directories are created only
    when a Sandbox tool is actually invoked, never merely by opening an Agent turn.
    """

    def __init__(self, task_id: int, base_root: str | Path, max_file_chars: int):
        if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id <= 0:
            raise SandboxPathError("Task workspace identity is invalid.")
        self.task_id = task_id
        self.base_root = Path(base_root).expanduser().absolute()
        self.task_root = self.base_root / f"task_{task_id}"
        self.max_file_chars = int(max_file_chars)
        self._resolved_base: Path | None = None
        self._resolved_root: Path | None = None

    def ensure_workspace(self) -> Path:
        """Lazily create/check the per-Task root without following a root link."""
        try:
            self.base_root.mkdir(parents=True, exist_ok=True)
            if _is_link_or_junction(self.base_root):
                raise SandboxPathError("Sandbox workspace root cannot be a link.")
            base_resolved = self.base_root.resolve(strict=True)
            if _is_link_or_junction(self.task_root):
                raise SandboxPathError("Task workspace cannot be a symlink or junction.")
            self.task_root.mkdir(exist_ok=True)
            if _is_link_or_junction(self.task_root):
                raise SandboxPathError("Task workspace cannot be a symlink or junction.")
            root_resolved = self.task_root.resolve(strict=True)
            if not root_resolved.is_relative_to(base_resolved):
                raise SandboxPathError("Task workspace escaped its configured root.")
            if not root_resolved.is_dir():
                raise SandboxPathError("Task workspace is not a directory.")
        except SandboxWorkspaceError:
            raise
        except OSError:
            raise SandboxWorkspaceError("Task workspace is unavailable.") from None
        self._resolved_base = base_resolved
        self._resolved_root = root_resolved
        return self.task_root

    def resolve_relative(
        self,
        raw_path: str,
        *,
        allow_root: bool = False,
        must_exist: bool = False,
    ) -> tuple[Path, str]:
        """Resolve a strictly relative path and reject links/junctions/escapes."""
        if (not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path
                or len(raw_path) > MAX_SANDBOX_PATH_CHARS):
            raise SandboxPathError("A bounded non-empty relative path is required.")
        if (raw_path.startswith(("/", "\\")) or "\\" in raw_path
                or ":" in raw_path or raw_path.startswith("~")):
            raise SandboxPathError("Only Sandbox-relative paths are allowed.")
        windows_path = PureWindowsPath(raw_path)
        if windows_path.is_absolute() or windows_path.drive:
            raise SandboxPathError("Absolute and drive-qualified paths are forbidden.")

        parts = raw_path.split("/")
        if any(part == ".." for part in parts):
            raise SandboxPathError("Path traversal is forbidden.")
        if len(parts) > MAX_SANDBOX_PATH_COMPONENTS:
            raise SandboxPathError("Sandbox path has too many components.")
        normalized_parts: list[str] = []
        for part in parts:
            if part in ("", "."):
                continue
            if (part.endswith((".", " ")) or any(c in _WINDOWS_INVALID for c in part)
                    or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED):
                raise SandboxPathError("Path contains an unsupported name.")
            normalized_parts.append(part)
        if not normalized_parts and not allow_root:
            raise SandboxPathError("This Sandbox tool requires a file/directory path.")

        root = self.ensure_workspace()
        candidate = root.joinpath(*normalized_parts)
        cursor = root
        for part in normalized_parts:
            cursor = cursor / part
            if _is_link_or_junction(cursor):
                raise SandboxPathError("Symlinks and junctions are not supported.")
        try:
            resolved = candidate.resolve(strict=must_exist)
        except OSError:
            raise SandboxPathError("Sandbox path could not be resolved.") from None
        assert self._resolved_root is not None
        if not resolved.is_relative_to(self._resolved_root):
            raise SandboxPathError("Resolved path escaped the Task workspace.")
        if must_exist and not candidate.exists():
            raise SandboxPathError("Sandbox path does not exist.")
        normalized = "." if not normalized_parts else "/".join(normalized_parts)
        return candidate, normalized

    def list_files(self, raw_path: str = ".") -> dict[str, Any]:
        directory, normalized = self.resolve_relative(
            raw_path, allow_root=True, must_exist=True
        )
        if not directory.is_dir():
            raise SandboxWorkspaceError("Sandbox path is not a directory.")
        entries: list[dict[str, Any]] = []
        truncated = False
        try:
            with os.scandir(directory) as iterator:
                for item in iterator:
                    if len(entries) >= MAX_DIRECTORY_ENTRIES:
                        truncated = True
                        break
                    path = Path(item.path)
                    if _is_link_or_junction(path):
                        kind, size = "symlink", None
                    elif item.is_dir(follow_symlinks=False):
                        kind, size = "directory", None
                    elif item.is_file(follow_symlinks=False):
                        kind, size = "file", item.stat(follow_symlinks=False).st_size
                    else:
                        kind, size = "unsupported", None
                    entries.append({"name": item.name, "type": kind, "size": size})
        except OSError:
            raise SandboxWorkspaceError("Sandbox directory could not be listed.") from None
        entries.sort(key=lambda entry: entry["name"])
        return {"path": normalized, "entries": entries, "truncated": truncated}

    def read_text_file(self, raw_path: str) -> dict[str, Any]:
        path, normalized = self.resolve_relative(raw_path, must_exist=True)
        if not path.is_file():
            raise SandboxWorkspaceError("Sandbox path is not a regular file.")
        byte_limit = self.max_file_chars * 4 + 4
        try:
            with path.open("rb") as stream:
                raw = stream.read(byte_limit)
                has_more = len(raw) == byte_limit and stream.read(1) != b""
        except OSError:
            raise SandboxWorkspaceError("Sandbox file could not be read.") from None

        # If the bounded byte read ends mid UTF-8 character, trim at most 3 bytes.
        decoded = None
        if not has_more:
            try:
                decoded = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                raise SandboxWorkspaceError(
                    "Only UTF-8 text files are supported."
                ) from None
        else:
            # A bounded read may end mid-codepoint. Trim at most three trailing
            # bytes, but never repair invalid bytes from a complete file.
            for end in range(len(raw), max(-1, len(raw) - 4), -1):
                try:
                    decoded = raw[:end].decode("utf-8", errors="strict")
                    break
                except UnicodeDecodeError:
                    continue
        if decoded is None:
            raise SandboxWorkspaceError("Only UTF-8 text files are supported.")
        if any(ord(char) < 32 and char not in "\\t\\n\\r" for char in decoded):
            raise SandboxWorkspaceError("Only UTF-8 text files are supported.")
        truncated = has_more or len(decoded) > self.max_file_chars
        return {
            "path": normalized,
            "content": decoded[:self.max_file_chars],
            "truncated": truncated,
        }

    def write_text_file(
        self, raw_path: str, content: str, overwrite: bool = False
    ) -> dict[str, Any]:
        if not isinstance(content, str):
            raise SandboxWorkspaceError("Sandbox file content must be text.")
        if len(content) > self.max_file_chars:
            raise SandboxFileTooLargeError("Sandbox file exceeds the configured size limit.")
        if type(overwrite) is not bool:
            raise SandboxWorkspaceError("overwrite must be boolean.")
        target, normalized = self.resolve_relative(raw_path)
        parent, _ = self.resolve_relative(
            "." if normalized.count("/") == 0 else normalized.rsplit("/", 1)[0],
            allow_root=True,
            must_exist=True,
        )
        if not parent.is_dir():
            raise SandboxWorkspaceError("Sandbox parent directory does not exist.")
        target_exists = target.exists()
        if target_exists and not overwrite:
            raise SandboxWorkspaceError("File exists; set overwrite=true to replace it.")
        encoded = content.encode("utf-8")
        temp_path: Path | None = None
        try:
            fd, temp_name = tempfile.mkstemp(prefix=".sandbox-write-", dir=parent)
            temp_path = Path(temp_name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            # Re-check the parent chain immediately before the atomic destination op.
            checked_target, _ = self.resolve_relative(normalized)
            if checked_target != target:
                raise SandboxPathError("Sandbox path changed during write.")
            if overwrite:
                os.replace(temp_path, target)
            else:
                # Hard-link is an atomic create-if-absent operation on the same filesystem.
                os.link(temp_path, target)
                temp_path.unlink()
        except FileExistsError:
            raise SandboxWorkspaceError(
                "File exists; set overwrite=true to replace it."
            ) from None
        except SandboxWorkspaceError:
            raise
        except OSError:
            raise SandboxWorkspaceError("Sandbox file could not be written atomically.") from None
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return {
            "path": normalized,
            "bytes_written": len(encoded),
            "overwritten": target_exists,
        }

    def make_directory(self, raw_path: str) -> dict[str, Any]:
        path, normalized = self.resolve_relative(raw_path, allow_root=True)
        try:
            if path.exists() and not path.is_dir():
                raise SandboxWorkspaceError("Path exists and is not a directory.")
            path.mkdir(parents=True, exist_ok=True)
            # Re-resolve after creation to reject any newly appeared link.
            self.resolve_relative(normalized, allow_root=True, must_exist=True)
        except SandboxWorkspaceError:
            raise
        except OSError:
            raise SandboxWorkspaceError("Sandbox directory could not be created.") from None
        return {"path": normalized, "created": True}


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction()) if callable(is_junction) else False
    except OSError:
        # Unable to inspect a component: fail closed.
        return True
