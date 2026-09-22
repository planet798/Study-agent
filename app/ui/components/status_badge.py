"""SAStatusBadge：状态徽标（只表达状态语义，不与 SATag 重复）。

SATag = 任意 metadata 标签；SAStatusBadge = 有限的状态枚举 + 固定展示文案。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

# status -> 展示文案
STATUSES: dict[str, str] = {
    "active": "进行中",
    "paused": "已暂停",
    "archived": "已归档",
    "planned": "计划中",
    "in_progress": "进行中",
    "completed": "已完成",
    "warning": "需关注",
}


class SAStatusBadge(QLabel):
    def __init__(self, status: str = "active", text: str | None = None, parent=None):
        super().__init__(text if text is not None else STATUSES.get(status, status), parent)
        if status not in STATUSES:
            raise ValueError(f"unknown status: {status!r}")
        self.setObjectName("SAStatusBadge")
        self._status = status
        self.setProperty("saStatus", status)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def status(self) -> str:
        return self._status

    def set_status(self, status: str, text: str | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status: {status!r}")
        self._status = status
        self.setProperty("saStatus", status)
        self.setText(text if text is not None else STATUSES.get(status, status))
        self.style().unpolish(self)
        self.style().polish(self)
