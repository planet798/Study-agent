"""Small JSON-lines bridge to pi-ai's provider/OAuth runtime.

The desktop application is Python, while pi-ai is TypeScript. Keeping OAuth
flows and provider-specific transports in the SDK avoids reimplementing them.
Credentials cross the process boundary only through stdin/stdout and live in the
OS keyring via AIConfigService.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from app.runtime_paths import bridge_dir, hidden_process_options, is_frozen, node_executable

BRIDGE_DIR = bridge_dir()
_PROFILE_LOCKS: dict[str, threading.Lock] = {}
_PROFILE_LOCKS_GUARD = threading.Lock()
_DISPLAY_TEXT_KEYS = frozenset({
    "content", "details", "error", "errormessage", "friendlymessage",
    "instructions", "label", "message", "placeholder", "text", "url",
    "usercode",
})


@contextmanager
def oauth_profile_lock(db_path: str | None, profile_id: int):
    """Serialize OAuth refreshes across threads and Study Agent processes."""
    db_key = hashlib.sha256(str(db_path or "default").encode()).hexdigest()[:16]
    lock_id = f"{db_key}-{int(profile_id)}"
    with _PROFILE_LOCKS_GUARD:
        lock = _PROFILE_LOCKS.setdefault(lock_id, threading.Lock())
    with lock:
        path = Path(tempfile.gettempdir()) / f"study-agent-oauth-{lock_id}.lock"
        with path.open("a+b") as lock_file:
            if os.name == "nt":
                import msvcrt
                lock_file.seek(0)
                if not lock_file.read(1):
                    lock_file.write(b"0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class PiAIBridgeError(RuntimeError):
    """Bridge unavailable or provider call failed."""


class PiAIBridge:
    def __init__(self, node: str | None = None):
        self.node = node or node_executable()

    @staticmethod
    def is_installed() -> bool:
        return (BRIDGE_DIR / "node_modules" / "@earendil-works" / "pi-ai").exists()

    @staticmethod
    def _credential_strings(value) -> list[str]:
        if isinstance(value, dict):
            return [secret for item in value.values() for secret in PiAIBridge._credential_strings(item)]
        if isinstance(value, list):
            return [secret for item in value for secret in PiAIBridge._credential_strings(item)]
        if isinstance(value, str) and len(value) >= 8:
            return [value]
        return []

    def call(
        self,
        request: dict,
        *,
        on_event: Callable[[dict], None] | None = None,
        on_prompt: Callable[[dict], str] | None = None,
        timeout: float = 180.0,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        if not self.is_installed():
            raise PiAIBridgeError(
                ("订阅登录组件缺失，请重新安装 Study Agent。" if is_frozen() else
                 "订阅登录组件尚未安装。请在项目目录运行：cd oauth_bridge && npm ci")
            )
        try:
            process = subprocess.Popen(
                [self.node, "index.mjs"], cwd=BRIDGE_DIR,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", bufsize=1, **hidden_process_options(),
            )
        except OSError as exc:
            raise PiAIBridgeError(
                "未找到 Node.js（需要 Node.js 22.19 或更新版本）"
            ) from exc
        assert process.stdin is not None and process.stdout is not None
        output_lines: queue.Queue[str | None] = queue.Queue()

        def read_stdout() -> None:
            try:
                for line in process.stdout:
                    output_lines.put(line)
            finally:
                output_lines.put(None)

        threading.Thread(target=read_stdout, daemon=True).start()
        secrets = self._credential_strings(request.get("credential"))
        secrets.extend(self._credential_strings(request.get("credentials")))

        def scrub(text: str) -> str:
            for secret in secrets:
                text = text.replace(secret, "***")
            return re.sub(r"(?i)bearer\s+\S+", "Bearer ***", text)

        def scrub_payload(value, *, display_text: bool = False):
            """Redact user-facing text while leaving data and credentials intact."""
            if isinstance(value, str):
                return scrub(value) if display_text else value
            if isinstance(value, dict):
                return {
                    key: scrub_payload(
                        child,
                        display_text=(
                            key not in {"credential", "credentials"}
                            and key.lower() in _DISPLAY_TEXT_KEYS
                        ),
                    )
                    for key, child in value.items()
                }
            if isinstance(value, list):
                return [scrub_payload(child, display_text=display_text) for child in value]
            return value

        try:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
            result = None
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if cancel_event and cancel_event.is_set():
                    process.kill()
                    raise PiAIBridgeError("登录已取消")
                if remaining <= 0:
                    process.kill()
                    raise PiAIBridgeError("模型登录或请求超时")
                try:
                    line = output_lines.get(timeout=min(0.2, remaining))
                except queue.Empty:
                    continue
                if line is None:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "event" in event:
                    if event["event"] == "prompt" and on_prompt:
                        try:
                            answer = on_prompt(scrub_payload(event))
                            reply = {"type": "prompt_result", "id": event["id"], "value": answer}
                        except Exception:
                            reply = {"type": "prompt_result", "id": event["id"], "cancelled": True}
                        process.stdin.write(json.dumps(reply, ensure_ascii=False) + "\n")
                        process.stdin.flush()
                    elif on_event:
                        on_event(scrub_payload(event))
                elif "result" in event:
                    result = event["result"]
                elif "error" in event:
                    raise PiAIBridgeError(scrub(str(event["error"])))
            return_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
            if return_code != 0:
                stderr = process.stderr.read() if process.stderr else ""
                raise PiAIBridgeError(scrub(stderr).strip() or "pi-ai 桥接进程执行失败")
            if result is None:
                raise PiAIBridgeError("pi-ai 桥接进程未返回结果")
            return scrub_payload(result)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise PiAIBridgeError("模型登录或请求超时") from exc
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    stream.close()

    def catalog(self) -> list[dict]:
        return self.call({"action": "catalog"}).get("providers", [])

    def login(self, provider: str, **callbacks) -> dict:
        return self.call({"action": "login", "provider": provider}, **callbacks)

    def complete(self, *, provider: str, model: str, credential: dict,
                 system_prompt: str = "", messages: list[dict] | None = None,
                 tools: list[dict] | None = None, temperature: float = 0.3,
                 max_tokens: int | None = None,
                 on_event: Callable[[dict], None] | None = None) -> dict:
        return self.call({
            "action": "complete", "provider": provider, "model": model,
            "credential": credential, "systemPrompt": system_prompt,
            "messages": messages or [], "tools": tools or [],
            "temperature": temperature, "maxTokens": max_tokens,
        }, on_event=on_event)
