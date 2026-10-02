"""API Key 安全存储（SecretStore）。

硬性要求：
- 真实 API Key 绝不写入 SQLite / JSON / 源码 / 日志；
- Windows 本地应用优先写入 Windows Credential Manager；
- 数据库只保存 ``secret_ref``（例如 ``ai-profile/3``）。

实现方式：使用 Python ``keyring`` 包（Windows 上自动走 Credential Manager）。
本模块对 keyring 后端不可用做优雅降级：
- ``get`` / ``delete``：后端异常时吞掉（视为“没有 Key”），并记录 ``last_error``；
- ``set``：后端异常时抛 :class:`SecretStoreError`，让 UI 明确告知用户保存失败
  （绝不静默丢弃 Key）。

本模块绝不打印 / 记录 Key 明文。
"""

from __future__ import annotations

import base64
import uuid
from typing import Any, Optional

# keyring service 名（写进 Credential Manager 的“服务”一栏）
SECRET_SERVICE_NAME = "study-agent"
SECRET_ACCOUNT_PREFIX = "ai-profile/"
# Windows Credential Manager limits credential blobs to 2560 bytes. Large OAuth
# payloads are base64-split into smaller sibling credentials under the same ref.
_SECRET_PART_PREFIX = "__study_agent_secret_v1__:"
_SECRET_PART_CHARS = 1800


class SecretStoreError(Exception):
    """SecretStore 无法读写（未安装 keyring / 后端不可用）时抛出。"""


def make_secret_ref(profile_id: int | str) -> str:
    """由 profile id 生成稳定的 secret_ref（数据库只存这个）。"""
    return f"{SECRET_ACCOUNT_PREFIX}{int(profile_id)}"


def secret_ref_suffix(secret_ref: str) -> str:
    """从 secret_ref 中取出后缀（profile id 字符串），非法返回空串。"""
    if not secret_ref:
        return ""
    if secret_ref.startswith(SECRET_ACCOUNT_PREFIX):
        return secret_ref[len(SECRET_ACCOUNT_PREFIX):]
    return secret_ref


class SecretStore:
    """keyring 薄封装。

    :param keyring_module: 可注入的 keyring 模块（测试用 fake）；None 时惰性 import。
    :param service_name: keyring service 名。
    """

    def __init__(
        self,
        keyring_module: Any | None = None,
        service_name: str = SECRET_SERVICE_NAME,
    ):
        self._keyring = keyring_module
        self.service_name = service_name
        # 最近一次后端错误信息（不含 Key），供 UI 诊断；成功时清空。
        self.last_error: str = ""

    # ---------- 内部 ----------

    def _mod(self) -> Any:
        if self._keyring is not None:
            return self._keyring
        try:
            import keyring as _kr

            return _kr
        except Exception as e:  # noqa: BLE001 - 未安装
            raise SecretStoreError(f"keyring 未安装：{e}") from e

    def _account(self, secret_ref: str) -> str:
        return secret_ref or ""

    @staticmethod
    def _manifest(value: str | None) -> tuple[str, int] | None:
        if not value or not value.startswith(_SECRET_PART_PREFIX):
            return None
        try:
            generation, count = value[len(_SECRET_PART_PREFIX):].rsplit(":", 1)
            return generation, int(count)
        except (ValueError, TypeError):
            return None

    def _delete_parts(self, secret_ref: str, manifest: tuple[str, int] | None) -> None:
        if manifest is None:
            return
        generation, count = manifest
        mod = self._mod()
        for index in range(count):
            try:
                mod.delete_password(
                    self.service_name,
                    f"{self._account(secret_ref)}#v1#{generation}#{index}",
                )
            except Exception:  # noqa: BLE001 - cleanup is best effort
                pass

    # ---------- 公共 API ----------

    def set(self, secret_ref: str, value: str) -> None:
        """写入 Key；失败抛 SecretStoreError（绝不静默丢弃）。"""
        if not secret_ref:
            raise SecretStoreError("secret_ref 不能为空")
        if value is None:
            value = ""
        try:
            mod = self._mod()
            account = self._account(secret_ref)
            old_value = mod.get_password(self.service_name, account)
            old_manifest = self._manifest(old_value)
            encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
            if len(value.encode("utf-8")) <= _SECRET_PART_CHARS:
                mod.set_password(self.service_name, account, value)
            else:
                generation = uuid.uuid4().hex
                parts = [encoded[i:i + _SECRET_PART_CHARS]
                         for i in range(0, len(encoded), _SECRET_PART_CHARS)]
                written = []
                try:
                    for index, part in enumerate(parts):
                        part_account = f"{account}#v1#{generation}#{index}"
                        mod.set_password(self.service_name, part_account, part)
                        written.append(part_account)
                    mod.set_password(
                        self.service_name, account,
                        f"{_SECRET_PART_PREFIX}{generation}:{len(parts)}",
                    )
                except Exception:
                    for part_account in written:
                        try:
                            mod.delete_password(self.service_name, part_account)
                        except Exception:
                            pass
                    raise
            self._delete_parts(secret_ref, old_manifest)
            self.last_error = ""
        except SecretStoreError:
            raise
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            raise SecretStoreError(
                "无法写入系统凭据存储，凭据未保存。"
                "请确认系统 keyring / Windows 凭据管理器可用。"
            ) from e

    def get(self, secret_ref: str) -> Optional[str]:
        """读取 Key；后端不可用返回 None（不抛异常，视为未配置）。"""
        if not secret_ref:
            return None
        try:
            mod = self._mod()
            account = self._account(secret_ref)
            value = mod.get_password(self.service_name, account)
            manifest = self._manifest(value)
            if manifest is not None:
                generation, count = manifest
                encoded = "".join(
                    mod.get_password(
                        self.service_name, f"{account}#v1#{generation}#{index}"
                    ) or ""
                    for index in range(count)
                )
                value = base64.b64decode(encoded, validate=True).decode("utf-8")
            self.last_error = ""
            return value
        except Exception as e:  # noqa: BLE001 - 后端不可用 / 无此条目
            self.last_error = str(e)
            return None

    def delete(self, secret_ref: str) -> None:
        """删除 Key（best-effort；不存在或后端不可用都不报错）。"""
        if not secret_ref:
            return
        try:
            mod = self._mod()
            account = self._account(secret_ref)
            manifest = self._manifest(mod.get_password(self.service_name, account))
            mod.delete_password(self.service_name, account)
            self._delete_parts(secret_ref, manifest)
            self.last_error = ""
        except Exception as e:  # noqa: BLE001 - 条目不存在 / 后端不可用
            self.last_error = str(e)

    def is_available(self) -> bool:
        """后端是否可用（用一次只读探测，不产生凭据）。"""
        try:
            mod = self._mod()
            # read 一个不可能存在的条目：真正可用的后端返回 None，fail 后端抛异常
            mod.get_password(self.service_name, "__probe__")
            return True
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            return False
