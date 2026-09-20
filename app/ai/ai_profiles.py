"""AI Provider Profile 数据访问层。

每个 Profile 表示一套可切换的 API 配置：

    id / display_name / provider_type / base_url / model /
    secret_ref / is_active / created_at / updated_at

**不存 API Key**：真实 Key 由 :class:`app.ai.secrets.SecretStore` 写入系统
keyring，本表只保存 ``secret_ref``。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from ..utils.date_utils import now_iso

# 第一版只支持 OpenAI-compatible（DeepSeek / USTC / OpenAI 等都是兼容 API）
PROVIDER_TYPE_OPENAI_COMPATIBLE = "openai_compatible"
SUPPORTED_PROVIDER_TYPES = (PROVIDER_TYPE_OPENAI_COMPATIBLE,)


@dataclass
class AIProfile:
    id: int
    display_name: str
    provider_type: str
    base_url: str
    model: str
    secret_ref: str
    is_active: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row) -> "AIProfile":
        return cls(
            id=int(row["id"]),
            display_name=row["display_name"] or "",
            provider_type=row["provider_type"] or PROVIDER_TYPE_OPENAI_COMPATIBLE,
            base_url=row["base_url"] or "",
            model=row["model"] or "",
            secret_ref=row["secret_ref"] or "",
            is_active=bool(row["is_active"]),
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )


class AIProfileRepository:
    """ai_profiles 表的读写（不含任何 Key）。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- 查询 ----------

    def list_all(self) -> list[AIProfile]:
        rows = self.conn.execute(
            "SELECT * FROM ai_profiles ORDER BY id ASC"
        ).fetchall()
        return [AIProfile.from_row(r) for r in rows]

    def get(self, profile_id: int) -> Optional[AIProfile]:
        row = self.conn.execute(
            "SELECT * FROM ai_profiles WHERE id = ?", (int(profile_id),)
        ).fetchone()
        return AIProfile.from_row(row) if row is not None else None

    def get_by_name(self, display_name: str) -> Optional[AIProfile]:
        row = self.conn.execute(
            "SELECT * FROM ai_profiles WHERE display_name = ?", (display_name,)
        ).fetchone()
        return AIProfile.from_row(row) if row is not None else None

    def get_active(self) -> Optional[AIProfile]:
        row = self.conn.execute(
            "SELECT * FROM ai_profiles WHERE is_active = 1 "
            "ORDER BY id ASC LIMIT 1"
        ).fetchone()
        return AIProfile.from_row(row) if row is not None else None

    def count(self) -> int:
        return int(
            self.conn.execute("SELECT COUNT(*) FROM ai_profiles").fetchone()[0]
        )

    # ---------- 写入 ----------

    def create(
        self,
        display_name: str,
        base_url: str,
        model: str,
        provider_type: str = PROVIDER_TYPE_OPENAI_COMPATIBLE,
        is_active: bool = False,
        secret_ref: str = "",
    ) -> AIProfile:
        name = (display_name or "").strip()
        if not name:
            raise ValueError("配置名称不能为空")
        if provider_type not in SUPPORTED_PROVIDER_TYPES:
            raise ValueError(f"不支持的 provider_type: {provider_type}")
        now = now_iso()
        cur = self.conn.execute(
            "INSERT INTO ai_profiles "
            "(display_name, provider_type, base_url, model, secret_ref, "
            " is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                provider_type,
                (base_url or "").strip(),
                (model or "").strip(),
                secret_ref or "",
                1 if is_active else 0,
                now,
                now,
            ),
        )
        self.conn.commit()
        profile = self.get(int(cur.lastrowid))
        assert profile is not None
        return profile

    def update(
        self,
        profile_id: int,
        *,
        display_name: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        provider_type: Optional[str] = None,
        secret_ref: Optional[str] = None,
    ) -> AIProfile:
        profile = self.get(profile_id)
        if profile is None:
            raise ValueError(f"AI 配置不存在: id={profile_id}")
        fields: dict[str, object] = {}
        if display_name is not None:
            name = display_name.strip()
            if not name:
                raise ValueError("配置名称不能为空")
            fields["display_name"] = name
        if base_url is not None:
            fields["base_url"] = base_url.strip()
        if model is not None:
            fields["model"] = model.strip()
        if provider_type is not None:
            if provider_type not in SUPPORTED_PROVIDER_TYPES:
                raise ValueError(f"不支持的 provider_type: {provider_type}")
            fields["provider_type"] = provider_type
        if secret_ref is not None:
            fields["secret_ref"] = secret_ref
        if not fields:
            return profile
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE ai_profiles SET {assignments} WHERE id = ?",
            (*fields.values(), int(profile_id)),
        )
        self.conn.commit()
        updated = self.get(profile_id)
        assert updated is not None
        return updated

    def delete(self, profile_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM ai_profiles WHERE id = ?", (int(profile_id),)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def set_active(self, profile_id: int) -> None:
        """把某 Profile 设为当前；全局最多一个 active（单事务）。"""
        if self.get(profile_id) is None:
            raise ValueError(f"AI 配置不存在: id={profile_id}")
        now = now_iso()
        self.conn.execute("BEGIN")
        try:
            self.conn.execute(
                "UPDATE ai_profiles SET is_active = 0, updated_at = ? "
                "WHERE is_active = 1",
                (now,),
            )
            self.conn.execute(
                "UPDATE ai_profiles SET is_active = 1, updated_at = ? WHERE id = ?",
                (now, int(profile_id)),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def clear_active(self) -> None:
        self.conn.execute("UPDATE ai_profiles SET is_active = 0")
        self.conn.commit()
