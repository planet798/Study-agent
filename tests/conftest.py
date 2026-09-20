"""pytest 共享 fixtures。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 必须在任何 Qt import 之前设置，保证无显示环境下也能创建 QApplication
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

# 确保 `app` 包可导入（从项目根目录运行 pytest 时也生效）
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database.connection import get_connection  # noqa: E402
from app.database.repository import TaskRepository  # noqa: E402
from app.services.task_service import TaskService  # noqa: E402
from app.services.date_service import DateService  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    """每个测试使用独立的临时数据库文件。"""
    c = get_connection(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture()
def repo(conn):
    return TaskRepository(conn)


@pytest.fixture()
def plan_repo(conn):
    """学习计划数据访问层（复用同一 connection）。"""
    from app.database.study_plan_repository import StudyPlanRepository

    return StudyPlanRepository(conn)


@pytest.fixture()
def task_service(repo):
    return TaskService(repo)


@pytest.fixture()
def date_service(repo):
    return DateService(repo)


@pytest.fixture()
def fixed_today():
    """GUI 测试统一使用的固定"今天"，避免依赖真实系统日期。"""
    return "2026-01-05"


@pytest.fixture()
def make_window(qtbot, repo, task_service, date_service, fixed_today):
    """构造一个绑定固定日期的 MainWindow 的工厂。

    用法：
        window = make_window()
        window2 = make_window()  # 每次独立
    """
    def _make():
        window = MainWindow(
            task_service=task_service,
            date_service=date_service,
            today_provider=lambda: fixed_today,
        )
        return window

    return _make


# ============================================================
# AI 设置中心测试 fixtures（fake keyring，绝不接触真实系统凭据）
# ============================================================


class FakeKeyring:
    """内存版 keyring，用于测试；不写真实系统凭据存储。"""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}
        self.fail_on_set = False

    def set_password(self, service, account, value):
        if self.fail_on_set:
            raise RuntimeError("fake keyring unavailable")
        self.store[(service, account)] = value

    def get_password(self, service, account):
        return self.store.get((service, account))

    def delete_password(self, service, account):
        if (service, account) in self.store:
            del self.store[(service, account)]


@pytest.fixture()
def fake_keyring():
    return FakeKeyring()


@pytest.fixture()
def ai_config_service(conn, fake_keyring):
    """绑定临时 DB + fake keyring 的 AIConfigService（env 清空）。"""
    from app.ai.config_service import AIConfigService
    from app.ai.secrets import SecretStore

    return AIConfigService(
        conn=conn,
        secret_store=SecretStore(keyring_module=fake_keyring),
        env={},
    )


@pytest.fixture()
def prompt_registry(conn):
    """DB 支持的 PromptRegistry（支持 override）。"""
    from app.ai.prompt_registry import PromptOverrideRepository, PromptRegistry

    return PromptRegistry(PromptOverrideRepository(conn))
