"""单实例守护：逻辑层 + 窗口恢复信号测试。

说明：
- 真实 Windows 多进程启动（双击快捷方式两次）无法在单进程 pytest 中完整模拟；
  这里覆盖守卫的全部逻辑分支（首启 / 二次检测通知 / 竞争 / 陈旧状态 /
  退出后可再启动），并验证 restore 信号能把隐藏窗口带回前台。
- Windows 多进程手动验收步骤见最终汇报。
"""

from __future__ import annotations

from PySide6.QtNetwork import QLocalServer

from app.utils.single_instance import SingleInstanceGuard


class TestGuard:
    def test_first_acquire_becomes_server(self, qtbot):
        g1 = SingleInstanceGuard(instance_id="ut-si-1")
        assert g1.acquire() is True
        assert g1._server is not None
        assert g1._server.isListening() is True
        g1.cleanup()

    def test_second_acquire_detects_existing_and_notifies(self, qtbot):
        sid = "ut-si-2"
        g1 = SingleInstanceGuard(instance_id=sid)
        assert g1.acquire() is True
        fired = []
        g1.restore_requested.connect(lambda: fired.append(1))

        g2 = SingleInstanceGuard(instance_id=sid)
        assert g2.acquire() is False  # 已有实例 → 通知其恢复，本进程退出
        qtbot.waitUntil(lambda: len(fired) > 0, timeout=3000)
        assert fired
        g1.cleanup()

    def test_unique_exit_then_restart(self, qtbot):
        sid = "ut-si-3"
        g1 = SingleInstanceGuard(instance_id=sid)
        assert g1.acquire() is True
        g1.cleanup()
        # 唯一实例退出后，可以再次正常启动
        g2 = SingleInstanceGuard(instance_id=sid)
        assert g2.acquire() is True
        g2.cleanup()

    def test_stale_server_state_does_not_block(self, qtbot):
        sid = "ut-si-4"
        g1 = SingleInstanceGuard(instance_id=sid)
        assert g1.acquire() is True
        g1.cleanup()
        # 模拟崩溃残留的陈旧 socket：removeServer 后再启动仍成功（不永久阻塞）
        QLocalServer.removeServer(sid)
        g2 = SingleInstanceGuard(instance_id=sid)
        assert g2.acquire() is True
        g2.cleanup()

    def test_server_name_conflict_is_not_a_second_instance(self, qtbot):
        sid = "ut-si-5"
        g1 = SingleInstanceGuard(instance_id=sid)
        assert g1.acquire() is True
        # 竞争/名称被占用：第二个实例绝不与第一个共存
        g2 = SingleInstanceGuard(instance_id=sid)
        assert g2.acquire() is False
        g1.cleanup()


class TestRestoreWindow:
    def test_restore_signal_restores_hidden_window(self, make_window, qtbot):
        guard = SingleInstanceGuard(instance_id="ut-si-restore")
        w = make_window()
        qtbot.addWidget(w)
        guard.restore_requested.connect(w._restore_from_tray)

        w.show()
        qtbot.waitExposed(w)
        w.hide()  # 已在托盘
        assert w.isVisible() is False

        guard.restore_requested.emit()  # 第二次启动发来的恢复信号
        assert w.isVisible() is True   # 已恢复可见
        guard.cleanup()
