"""单个 JSON-lines 桥接进程；结果交付不依赖进程退出。"""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
import uuid

from app.runtime_paths import bridge_dir, hidden_process_options


class BridgeProcessError(RuntimeError):
    pass


class BridgeProcess:
    def __init__(self, node: str):
        self.busy = False
        self.ready = threading.Event()
        self._queues = {}
        self._guard = threading.Lock()
        self._write_lock = threading.Lock()
        self.process = subprocess.Popen(
            [node, "index.mjs", "--server"], cwd=bridge_dir(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            **hidden_process_options(),
        )
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def alive(self):
        return self.process.poll() is None

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if event.get("event") == "ready":
                    self.ready.set()
                with self._guard:
                    target = self._queues.get(event.get("requestId"))
                if target is not None:
                    target.put(event)
        finally:
            self.ready.set()
            with self._guard:
                for target in self._queues.values():
                    target.put(None)

    def _write(self, payload):
        with self._write_lock:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def call(self, request, *, deadline, on_event=None, cancel_event=None):
        if not self.ready.wait(max(0, deadline - time.monotonic())) or not self.alive():
            raise BridgeProcessError("订阅组件启动失败或超时")
        request_id = uuid.uuid4().hex
        output = queue.Queue()
        with self._guard:
            self._queues[request_id] = output
        try:
            self._write({**request, "requestId": request_id})
            while True:
                if cancel_event and cancel_event.is_set():
                    self._write({"action": "cancel", "requestId": request_id})
                    raise BridgeProcessError("模型请求已取消")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._write({"action": "cancel", "requestId": request_id})
                    raise BridgeProcessError("模型请求超时")
                try:
                    event = output.get(timeout=min(.1, remaining))
                except queue.Empty:
                    continue
                if event is None:
                    raise BridgeProcessError("订阅组件异常退出，请重试")
                if "error" in event:
                    raise BridgeProcessError(str(event["error"]))
                if "result" in event:
                    return event["result"]
                if on_event:
                    on_event(event)
        finally:
            with self._guard:
                self._queues.pop(request_id, None)

    def close(self):
        if self.alive():
            self.process.kill()
        self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()
        self._reader.join(timeout=1)
