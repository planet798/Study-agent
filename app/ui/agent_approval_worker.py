"""Thread-local SQLite executor for an explicit Workspace approval click."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from ..database.connection import get_connection


class AgentApprovalWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, db_path, approval_service_factory, approval_id: int, parent=None):
        super().__init__(parent)
        self._db_path = db_path
        self._factory = approval_service_factory
        self._approval_id = int(approval_id)

    def run(self):
        conn = None
        try:
            conn = get_connection(self._db_path)
            result = self._factory(conn).approve_and_execute(self._approval_id)
            if result["status"] == "failed":
                self.failed.emit("任务状态已变化或执行未成功，请刷新后查看。")
            else:
                self.succeeded.emit(result)
        except Exception:
            self.failed.emit("审批暂未完成，请稍后重试。")
        finally:
            if conn is not None:
                conn.close()
