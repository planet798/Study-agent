"""Prompt Registry：内置默认 + 用户覆盖 + 安全渲染 + 变量校验。

模型：
    effective_prompt = override（若存在） else builtin_default

- 系统默认 Prompt 永远保留在代码里（:mod:`prompt_defaults`）；
- 用户修改只写 ``prompt_overrides`` 表，``git pull`` 不会覆盖；
- “恢复默认” = 删除 override。

渲染安全性：
- 只做简单的 ``{{variable}}`` 替换；
- 绝不使用 eval / exec / str.format / Jinja（避免任意代码执行与 KeyError）。
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from typing import Optional

from ..utils.date_utils import now_iso
from .prompt_defaults import (
    DEFAULT_PROMPT_DEFINITIONS,
    PromptDefinition,
    get_default_definition,
)

# {{ variable_name }}：变量名限定为标识符，避免复杂表达式
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_FULL_LINE_PLACEHOLDER_RE = re.compile(
    r"^\s*\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}\s*$"
)
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


class PromptError(Exception):
    """Prompt Registry 相关错误基类。"""


class UnknownPromptKeyError(PromptError):
    """使用了未注册的 Prompt key。"""


class PromptRenderError(PromptError):
    """渲染时缺少变量。"""


class PromptValidationError(PromptError):
    """保存的模板缺少必需变量或包含未知变量。"""

    def __init__(
        self,
        message: str,
        missing_required: list[str] | None = None,
        unknown_variables: list[str] | None = None,
    ):
        super().__init__(message)
        self.missing_required = list(missing_required or [])
        self.unknown_variables = list(unknown_variables or [])


@dataclass
class PromptValidation:
    missing_required: list[str]
    unknown_variables: list[str]

    @property
    def ok(self) -> bool:
        return not self.missing_required and not self.unknown_variables

    def message(self) -> str:
        parts: list[str] = []
        if self.missing_required:
            parts.append(
                "缺少必需变量：" + "、".join(f"{{{{{n}}}}}" for n in self.missing_required)
            )
        if self.unknown_variables:
            parts.append(
                "未知变量：" + "、".join(f"{{{{{n}}}}}" for n in self.unknown_variables)
            )
        return "\n".join(parts)


@dataclass
class PromptPreview:
    key: str
    definition: PromptDefinition
    template: str
    is_customized: bool
    context: dict
    rendered: str


# ============================================================
# 纯函数：提取 / 校验 / 渲染
# ============================================================


def extract_variables(template: str) -> list[str]:
    """按出现顺序返回模板中的变量名（去重）。"""
    seen: list[str] = []
    for match in _PLACEHOLDER_RE.finditer(template or ""):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def validate_template(
    definition: PromptDefinition, template: str
) -> PromptValidation:
    """校验模板的必需变量与未知变量（不修改模板）。"""
    found = set(extract_variables(template))
    known = set(definition.all_variables)
    missing = [v for v in definition.required_variables if v not in found]
    unknown = sorted(found - known)
    return PromptValidation(missing_required=missing, unknown_variables=unknown)


def render_template(template: str, context: dict) -> str:
    """安全渲染：仅替换 ``{{variable}}``。

    - 整行只有一个变量且值为空 → 删除该行（避免多余空行）；
    - 其它位置内联替换；
    - 连续 3 个以上换行折叠为一个空行；
    - 缺少变量抛 :class:`PromptRenderError`（绝不静默留白）。
    """
    lines = (template or "").split("\n")
    out: list[str] = []
    for line in lines:
        full = _FULL_LINE_PLACEHOLDER_RE.match(line)
        if full:
            name = full.group(1)
            if name not in context:
                raise PromptRenderError(f"渲染缺少变量 {{{{{name}}}}}")
            value = context[name]
            value = "" if value is None else str(value)
            if value == "":
                continue
            out.append(value)
            continue

        def _replace(match: re.Match) -> str:
            name = match.group(1)
            if name not in context:
                raise PromptRenderError(f"渲染缺少变量 {{{{{name}}}}}")
            value = context[name]
            return "" if value is None else str(value)

        out.append(_PLACEHOLDER_RE.sub(_replace, line))

    result = "\n".join(out)
    result = _MULTI_NEWLINE_RE.sub("\n\n", result)
    return result.strip("\n")


# ============================================================
# Override 数据访问
# ============================================================


class PromptOverrideRepository:
    """prompt_overrides 表读写。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, prompt_key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT content FROM prompt_overrides WHERE prompt_key = ?",
            (prompt_key,),
        ).fetchone()
        if row is None:
            return None
        return row[0]

    def list_all(self) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT prompt_key, content FROM prompt_overrides"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def updated_at(self, prompt_key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT updated_at FROM prompt_overrides WHERE prompt_key = ?",
            (prompt_key,),
        ).fetchone()
        return row[0] if row is not None else None

    def set(self, prompt_key: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO prompt_overrides (prompt_key, content, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(prompt_key) DO UPDATE SET "
            "content = excluded.content, updated_at = excluded.updated_at",
            (prompt_key, content, now_iso()),
        )
        self.conn.commit()

    def delete(self, prompt_key: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM prompt_overrides WHERE prompt_key = ?", (prompt_key,)
        )
        self.conn.commit()
        return cur.rowcount > 0


# ============================================================
# Registry
# ============================================================


class PromptRegistry:
    """统一 Prompt 入口。

    :param override_repo: :class:`PromptOverrideRepository`；None 表示“只用默认”
        （便于测试与向后兼容）。
    :param definitions: 自定义定义集合（默认使用内置定义）。
    """

    def __init__(
        self,
        override_repo: PromptOverrideRepository | None = None,
        definitions: tuple[PromptDefinition, ...] | None = None,
    ):
        self._override_repo = override_repo
        defs = definitions if definitions is not None else DEFAULT_PROMPT_DEFINITIONS
        self._definitions = {d.key: d for d in defs}
        self._order = [d.key for d in defs]
        # override 内存缓存：渲染发生在 worker 线程时绝不读 SQLite（
        # 避免跨线程连接）。构造时（主线程）一次性加载；保存/恢复同步更新。
        self._lock = threading.RLock()
        self._cache: dict[str, str] = {}
        if override_repo is not None:
            try:
                self._cache = dict(override_repo.list_all())
            except Exception:  # noqa: BLE001 - 读失败则视为无 override
                self._cache = {}

    # ---------- 元数据 ----------

    def definitions(self) -> list[PromptDefinition]:
        return [self._definitions[k] for k in self._order]

    def definition(self, key: str) -> PromptDefinition:
        if key not in self._definitions:
            # 内置定义缺失时回退到代码默认（对外仍是稳定 key）
            try:
                return get_default_definition(key)
            except KeyError:
                raise UnknownPromptKeyError(key) from None
        return self._definitions[key]

    def keys(self) -> list[str]:
        return list(self._order)

    def categories(self) -> list[str]:
        seen: list[str] = []
        for key in self._order:
            cat = self._definitions[key].category
            if cat not in seen:
                seen.append(cat)
        return seen

    def by_category(self) -> dict[str, list[PromptDefinition]]:
        out: dict[str, list[PromptDefinition]] = {}
        for key in self._order:
            d = self._definitions[key]
            out.setdefault(d.category, []).append(d)
        return out

    # ---------- effective template ----------

    def get_override(self, key: str) -> Optional[str]:
        with self._lock:
            return self._cache.get(key)

    def is_customized(self, key: str) -> bool:
        return self.get_override(key) is not None

    def default_template(self, key: str) -> str:
        return self.definition(key).default_template

    def effective_template(self, key: str) -> str:
        override = self.get_override(key)
        if override is not None:
            return override
        return self.default_template(key)

    def override_updated_at(self, key: str) -> Optional[str]:
        if self._override_repo is None:
            return None
        with self._lock:
            if key not in self._cache:
                return None
        return self._override_repo.updated_at(key)

    # ---------- 写 override（带校验） ----------

    def validate(self, key: str, template: str) -> PromptValidation:
        return validate_template(self.definition(key), template)

    def set_override(self, key: str, content: str) -> None:
        """保存用户自定义模板；校验不通过抛 PromptValidationError。"""
        if self._override_repo is None:
            raise PromptError("当前 PromptRegistry 未连接数据库，无法保存")
        definition = self.definition(key)
        result = validate_template(definition, content)
        if not result.ok:
            raise PromptValidationError(result.message(), result.missing_required,
                                        result.unknown_variables)
        self._override_repo.set(key, content)
        with self._lock:
            self._cache[key] = content

    def reset(self, key: str) -> bool:
        """恢复系统默认（删除 override）。"""
        if self._override_repo is None:
            return False
        deleted = self._override_repo.delete(key)
        with self._lock:
            self._cache.pop(key, None)
        return deleted

    # ---------- 渲染 ----------

    def render(self, key: str, context: dict) -> str:
        """用 effective template 渲染最终 Prompt。"""
        return render_template(self.effective_template(key), context or {})

    def preview(self, key: str, context: dict) -> PromptPreview:
        definition = self.definition(key)
        template = self.effective_template(key)
        return PromptPreview(
            key=key,
            definition=definition,
            template=template,
            is_customized=self.is_customized(key),
            context=dict(context or {}),
            rendered=render_template(template, context or {}),
        )


# ============================================================
# 模块级默认 Registry（服务未注入时使用；只有默认、无 override）
# ============================================================

_default_registry: PromptRegistry | None = None


def default_prompt_registry() -> PromptRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = PromptRegistry()
    return _default_registry
