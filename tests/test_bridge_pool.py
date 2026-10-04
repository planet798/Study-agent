import shutil
import time

import pytest

from app.ai.bridge_pool import BridgePool
from app.ai.bridge_process import BridgeProcess, BridgeProcessError


class Worker:
    def __init__(self, node):
        self.busy = False
        self.closed = False
        self.calls = 0

    def alive(self):
        return not self.closed

    def close(self):
        self.closed = True

    def call(self, request, **kwargs):
        self.calls += 1
        if request.get("fail"):
            raise BridgeProcessError("failed")
        return request


def test_profile_reuse_bounded_eviction_and_no_resend():
    created = []
    def factory(node):
        worker = Worker(node)
        created.append(worker)
        return worker
    pool = BridgePool(factory)
    assert pool.call("node", "a", {"credential": "first"})["credential"] == "first"
    assert pool.call("node", "a", {"credential": "new"})["credential"] == "new"
    assert len(created) == 1 and created[0].calls == 2
    pool.call("node", "b", {})
    pool.call("node", "c", {})
    assert len(pool._workers) == 2 and created[0].closed
    with pytest.raises(BridgeProcessError):
        pool.call("node", "c", {"fail": True})
    assert created[-1].calls == 2 and created[-1].closed
    pool.call("node", "c", {})
    assert len(created) == 4
    pool.close()
    assert all(worker.closed for worker in created)


def test_result_returns_with_live_process_and_stale_packets_are_ignored(tmp_path, monkeypatch):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for real IPC regression")
    (tmp_path / "index.mjs").write_text('''
import { createInterface } from 'node:readline';
const input = createInterface({input:process.stdin});
console.log(JSON.stringify({event:'ready'}));
input.on('line', line => {
  const req=JSON.parse(line);
  console.log(JSON.stringify({requestId:'stale',result:{wrong:true}}));
  console.log(JSON.stringify({requestId:req.requestId,result:{marker:req.marker}}));
});
setInterval(()=>{},10000);
''')
    monkeypatch.setattr("app.ai.bridge_process.bridge_dir", lambda: tmp_path)
    worker = BridgeProcess(node)
    try:
        start = time.monotonic()
        assert worker.call({"marker": "own"}, deadline=start+3) == {"marker": "own"}
        assert worker.alive()  # 服务仍存活，交付不再等 EOF 或空闲连接退出。
        assert time.monotonic() - start < 3
    finally:
        worker.close()


def test_timeout_stops_inflight_worker_without_replay(tmp_path, monkeypatch):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required")
    (tmp_path / "index.mjs").write_text("console.log(JSON.stringify({event:'ready'}));setInterval(()=>{},10000);")
    monkeypatch.setattr("app.ai.bridge_process.bridge_dir", lambda: tmp_path)
    pool = BridgePool()
    try:
        with pytest.raises(BridgeProcessError, match="超时"):
            pool.call(node, "profile", {}, timeout=.3)
        assert not pool._workers
    finally:
        pool.close()
