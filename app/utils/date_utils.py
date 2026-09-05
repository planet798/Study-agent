"""日期工具函数。

统一使用 YYYY-MM-DD 字符串表示"日"，时间使用 ISO 格式字符串。
所有日期均为本地日期。

日期注入：默认使用系统当前日期；测试/开发可通过 set_today_provider /
override_today 覆盖"今天"，无需修改系统时间。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Callable, Iterator, Union

_DATE_FORMAT = "%Y-%m-%d"


def _system_today() -> str:
    """默认提供者：系统当前日期。"""
    return date.today().strftime(_DATE_FORMAT)


_today_provider: Callable[[], str] = _system_today


def today() -> str:
    """返回今天的日期字符串，如 2026-01-15（经 provider 统一获取）。"""
    return _today_provider()


def set_today_provider(provider: Union[Callable[[], str], str, None]) -> None:
    """覆盖"今天"的来源（仅测试/开发用）。

    :param provider: 可调用（返回 YYYY-MM-DD 字符串）、固定日期字符串、或 None。
        None 表示恢复为系统当前日期。
    """
    global _today_provider
    if provider is None:
        _today_provider = _system_today
    elif isinstance(provider, str):
        fixed = provider
        _today_provider = lambda: fixed
    else:
        _today_provider = provider


def reset_today_provider() -> None:
    """恢复为系统当前日期（与 set_today_provider(None) 等价）。"""
    set_today_provider(None)


@contextmanager
def override_today(date_str: str) -> Iterator[None]:
    """临时把"今天"覆盖为 date_str，离开上下文自动恢复。

    用法：
        with override_today("2026-09-05"):
            assert today() == "2026-09-05"
    """
    previous = _today_provider
    set_today_provider(date_str)
    try:
        yield
    finally:
        # 恢复调用方原本的 provider（系统日期或外层覆盖值）
        set_today_provider(previous)


def from_date(d: date) -> str:
    """将 date 对象转为日期字符串。"""
    return d.strftime(_DATE_FORMAT)


def to_date(date_str: str) -> date:
    """将日期字符串转为 date 对象。"""
    return datetime.strptime(date_str, _DATE_FORMAT).date()


def add_days(date_str: str, days: int) -> str:
    """返回 date_str 加减 days 天后的日期字符串。"""
    return from_date(to_date(date_str) + timedelta(days=days))


def now_iso() -> str:
    """返回当前本地时间的 ISO 字符串（秒精度），如 2026-01-15T10:30:00。"""
    return datetime.now().replace(microsecond=0).isoformat()


def to_display(date_str: str) -> str:
    """将日期字符串转为更可读的显示格式，如 2026-01-15 → 01-15。"""
    return to_date(date_str).strftime("%m-%d")


def week_start(date_str: str) -> str:
    """返回包含 date_str 的周（周一起始）的第一天。"""
    d = to_date(date_str)
    return from_date(d - timedelta(days=d.weekday()))


def week_end(date_str: str) -> str:
    """返回包含 date_str 的周的周日（周日结束）。"""
    return add_days(week_start(date_str), 6)


def month_range(year: int, month: int) -> tuple[str, str]:
    """返回某年某月的 (首日, 末日)。"""
    first = date(year, month, 1)
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return from_date(first), from_date(last)
