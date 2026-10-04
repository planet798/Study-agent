"""最多两个按配置隔离的桥接进程；只重启尚未发送请求的死进程。"""
from __future__ import annotations

import atexit
import hashlib
import threading
import time
from collections import OrderedDict

from .bridge_process import BridgeProcess, BridgeProcessError


def profile_bridge_key(db_path, profile_id):
    return hashlib.sha256(f"{db_path}:{profile_id}".encode()).hexdigest()


class BridgePool:
    def __init__(self, factory=BridgeProcess):
        self._factory = factory
        self._workers = OrderedDict()
        self._condition = threading.Condition()
        self._closed = False

    def call(self, node, key, request, *, timeout=180, on_event=None, cancel_event=None):
        deadline = time.monotonic() + timeout
        identity = (node, key)
        with self._condition:
            while True:
                if self._closed:
                    raise BridgeProcessError("订阅组件正在退出")
                if cancel_event and cancel_event.is_set():
                    raise BridgeProcessError("模型请求已取消")
                worker = self._workers.get(identity)
                if worker is not None and not worker.busy:
                    if not worker.alive():
                        worker.close()
                        del self._workers[identity]
                        worker = None
                    else:
                        break
                if worker is None:
                    if len(self._workers) >= 2:
                        idle = next(((k, w) for k, w in self._workers.items() if not w.busy), None)
                        if idle:
                            self._workers.pop(idle[0]).close()
                    if len(self._workers) < 2:
                        worker = self._factory(node)
                        self._workers[identity] = worker
                        break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BridgeProcessError("等待订阅组件超时")
                self._condition.wait(min(.1, remaining))
            worker.busy = True
            self._workers.move_to_end(identity)
        try:
            return worker.call(request, deadline=deadline, on_event=on_event, cancel_event=cancel_event)
        except BaseException:
            worker.close()  # 禁止自动重发已经交给服务端的请求。
            with self._condition:
                self._workers.pop(identity, None)
            raise
        finally:
            with self._condition:
                worker.busy = False
                self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            workers = list(self._workers.values())
            self._workers.clear()
            self._condition.notify_all()
        for worker in workers:
            worker.close()


POOL = BridgePool()
atexit.register(POOL.close)
