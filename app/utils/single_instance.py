"""Study Agent 单实例守护（跨启动可靠，不依赖 PID 文件）。

方案：QLocalServer / QLocalSocket（Qt 原生本地 IPC）。
- 名称固定；Windows 下为每个用户会话的命名管道，Linux/macOS 为 unix socket。
- 启动即尝试“连接已有实例”：
    · 连接成功 → 已有实例存活 → 通知其恢复前台 → 本进程直接退出；
    · 连接失败 → 本进程成为唯一实例，监听同名 server。
- 竞争：两个进程同时启动都连接失败时，只有一方 listen 成功；
  另一方 listen 报“名称被占用” → 视为已有实例，发 restore 后退出。
- 陈旧状态：listen 前先 removeServer（尽力清理崩溃残留）。
  崩溃后 OS 会回收命名管道，不会造成永久无法启动。
- 不含锁文件、不写库、不产生残留。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

# 固定实例名；加入简短的“会话作用域”命名以减少冲突（Qt 默认 UserScope）
INSTANCE_ID = "study-agent-instance-v1"


class SingleInstanceGuard(QObject):
    """单实例守卫：连接已有实例 or 成为监听方，并转发“恢复前台”请求。"""

    # 已有第二实例启动时发出的恢复信号（应连接到窗口恢复/激活处理）
    restore_requested = Signal()

    def __init__(self, instance_id: str = INSTANCE_ID, parent=None):
        super().__init__(parent)
        self.instance_id = instance_id
        self._server: QLocalServer | None = None
        self._socket: QLocalSocket | None = None

    # ---------- 生命周期 ----------

    def acquire(self) -> bool:
        """尝试成为唯一实例。

        :return: True 表示本进程是唯一实例（可继续启动）；
                 False 表示已有实例在运行（已通知其恢复，本进程应退出）。
        """
        # 1) 已存活实例 → 通知恢复并退出
        if self._notify_restore():
            return False
        # 2) 无存活实例 → 成为监听方（先清理可能残留的陈旧 socket）
        QLocalServer.removeServer(self.instance_id)
        self._server = QLocalServer(self)
        if not self._server.listen(self.instance_id):
            # 名称被占用（竞争输掉）→ 当作已有实例，再尽力通知一次
            self._notify_restore()
            return False
        self._server.newConnection.connect(self._on_new_connection)
        return True

    def cleanup(self) -> None:
        """正常退出：关闭 server（释放监听）。不删除任何残留文件。"""
        if self._server is not None:
            self._server.close()
            self._server = None

    # ---------- 内部 ----------

    def _notify_restore(self) -> bool:
        """尝试连接已有实例并发送 restore；成功返回 True。"""
        sock = QLocalSocket(self)
        sock.connectToServer(self.instance_id)
        if not sock.waitForConnected(300):
            return False
        sock.write(b"restore")
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True

    def _on_new_connection(self) -> None:
        conn = self._server.nextPendingConnection()
        if conn is None:
            return
        try:
            conn.readyRead.connect(
                lambda c=conn: self._on_ready_read(c)
            )
        except RuntimeError:  # noqa: BLE001 - 连接已被清理
            pass

    def _on_ready_read(self, conn) -> None:
        data = bytes(conn.readAll()).decode("utf-8", "ignore")
        try:
            conn.disconnectFromServer()
        except RuntimeError:  # noqa: BLE001 - 连接已断开
            pass
        if "restore" in data:
            self.restore_requested.emit()
