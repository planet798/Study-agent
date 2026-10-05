"""冻结程序入口：安全启动日志、错误提示及无网络构建自检。"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from app.runtime_paths import logs_dir, resource_root
from app.version import VERSION


def _record_error(exc_type, exc, tb, *, notify=True):
    # 不写异常消息/局部变量，避免第三方错误暴露凭据或对话正文。
    lines = [f"{datetime.now().isoformat()} {exc_type.__name__}"]
    lines.extend(f"{Path(frame.filename).name}:{frame.lineno} {frame.name}" for frame in traceback.extract_tb(tb))
    try:
        directory = logs_dir()
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / "startup.log"
        if log.exists() and log.stat().st_size > 1_000_000:
            log.replace(directory / "startup.previous.log")
        with log.open("a", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
        location = str(log)
    except Exception:
        location = "无法写入启动日志，请检查用户目录权限。"
    message = f"Study Agent 启动或运行失败（{exc_type.__name__}）。\n\n诊断日志：{location}\n学习数据保留，请勿删除数据库。"
    if notify and os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Study Agent", 0x10)


def self_test(report: Path) -> int:
    """只使用临时资源/离线 catalog，不读写真实学习库或凭据。"""
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"ok": False, "version": VERSION, "stage": "imports"}), encoding="utf-8")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon
    from app.ai.pi_ai_bridge import PiAIBridge
    from app.ui.design.theme_manager import render_theme

    qt = QApplication.instance() or QApplication([])
    icon = QIcon(str(resource_root() / "assets/study-agent.ico"))
    if icon.isNull():
        raise RuntimeError("Missing application icon")
    from app.ui.design.icons import icon as fluent_icon
    if fluent_icon("home").isNull():
        raise RuntimeError("Missing SVG renderer/resources")
    # 样式加载函数在不同主题间均需可用。
    if not render_theme("dark") or not render_theme("light"):
        raise RuntimeError("Missing styles")
    import keyring
    from keyring.backends.Windows import WinVaultKeyring
    if not isinstance(keyring.get_keyring(), WinVaultKeyring):
        raise RuntimeError("Windows credential backend unavailable")
    report.write_text(json.dumps({"ok": False, "version": VERSION, "stage": "oauth_bridge"}), encoding="utf-8")
    catalog = PiAIBridge().call({"action": "catalog"}, timeout=45)
    if not catalog.get("providers"):
        raise RuntimeError("Empty provider catalog")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"ok": True, "version": VERSION, "providers": len(catalog["providers"])}, indent=2), encoding="utf-8")
    qt.quit()
    return 0


def run() -> int:
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    sys.excepthook = _record_error
    self_check = len(sys.argv) == 3 and sys.argv[1] == "--self-test"
    try:
        if self_check:
            return self_test(Path(sys.argv[2]))
        from app.updates import startup
        if not startup.initialize(sys.argv):
            return 0
        from app.main import main
        result = main()
        if result != 0:
            startup.fail_startup()
        return result
    except Exception:
        from app.updates.startup import fail_startup
        fail_startup()
        _record_error(*sys.exc_info(), notify=not self_check)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
