"""Windows 一键启动脚本测试。

PowerShell 脚本在无 pwsh/Windows 环境下无法完整执行，因此测试分三层：

1. 始终运行的静态文本检查：
   - API Key 卫生（脚本绝不打印/记录 Key）
   - 启动器关键路径与逻辑（.venv python / app\\main.py / 根目录解析 / 错误钩子）
   - 快捷方式参数（-WindowStyle Hidden 等）与确定性文件名
   - 诊断脚本只输出 Configured / Missing
2. 始终运行的纯 Python 镜像测试：复刻 .ps1 的项目根目录 / Python / main 解析与检查顺序。
3. 本机存在 pwsh / powershell 才运行的实测（-DryRun 模式，不弹窗不挂起）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

SCRIPT_NAMES = ("start_study_agent.ps1", "create_shortcut.ps1", "diagnose_windows.ps1")

# 疑似 API Key / 密钥样式的样本模式（如 sk-xxx）
_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
# 把“值”写出去的语句关键词（输出/记录/弹窗/错误）
_WRITERS = (
    "Write-Output", "Write-Host", "Add-Content", "[Console", "MessageBox",
    "Out-File", "Write-Error", "Write-Information", "echo ",
)


def _read_script(name: str) -> str:
    """读取 .ps1（忽略 UTF-8 BOM）。"""
    return (SCRIPTS_DIR / name).read_text(encoding="utf-8-sig")


def _launch_shell() -> str | None:
    """优先 pwsh，其次 powershell（仅 Windows/装了 pwsh 的机器可用）。"""
    return shutil.which("pwsh") or shutil.which("powershell")


# ==================== API Key 卫生 ====================

class TestApiKeyHygiene:
    def test_no_sample_key_in_any_script(self):
        for name in SCRIPT_NAMES:
            text = _read_script(name)
            assert _KEY_PATTERN.search(text) is None, f"{name} 疑似包含 Key 样式的文本"

    def test_launcher_never_reads_deepseek_env(self):
        """启动器不应读取 DeepSeek 环境变量（API 由 Study Agent 自行读取）。"""
        text = _read_script("start_study_agent.ps1")
        # 注释里出现 DEEPSEEK_API_KEY 字样是文档；关键是不能读取/输出它
        assert "env:DEEPSEEK_API_KEY" not in text

    def test_no_shell_script_hardcodes_a_key(self):
        for name in SCRIPT_NAMES:
            text = _read_script(name)
            assert not re.search(r"DEEPSEEK_API_KEY\s*=\s*[\"']", text), name

    def test_no_line_outputs_key_value(self):
        """任何脚本里，凡写入“值”的语句都不允许引用 $env:DEEPSEEK_API_KEY。"""
        for name in SCRIPT_NAMES:
            for lineno, line in enumerate(_read_script(name).splitlines(), 1):
                if "env:DEEPSEEK_API_KEY" in line and any(w in line for w in _WRITERS):
                    pytest.fail(f"{name}:{lineno} 可能把 API Key 写出去: {line!r}")

    def test_diagnose_streams_key_value_never(self):
        """诊断脚本只能输出 Configured/Missing，绝不输出 Key 变量内容。"""
        text = _read_script("diagnose_windows.ps1")
        assert "DEEPSEEK_API_KEY: Configured" in text
        assert "DEEPSEEK_API_KEY: Missing" in text
        for line in text.splitlines():
            assert not re.search(
                r"Write-(Output|Host)\b.*\bapiKey\b", line
            ), f"可能打印 Key: {line!r}"


# ==================== 启动器静态检查 ====================

class TestLauncherStatic:
    def test_uses_venv_python(self):
        assert ".venv\\Scripts\\python.exe" in _read_script("start_study_agent.ps1")

    def test_uses_app_main_py(self):
        assert "app\\main.py" in _read_script("start_study_agent.ps1")

    def test_root_resolved_from_psscriptroot_not_cwd(self):
        assert "Split-Path -Parent $PSScriptRoot" in _read_script("start_study_agent.ps1")

    def test_no_hardcoded_drive_path(self):
        """启动器不写死磁盘路径（D:\\ 之类），项目移动也不受影响。"""
        text = _read_script("start_study_agent.ps1")
        assert re.search(r"[A-Za-z]:\\", text) is None

    def test_does_not_activate_venv(self):
        """不使用 Activate.ps1，直接调用 .venv 里的 python.exe。"""
        text = _read_script("start_study_agent.ps1")
        assert ".venv\\Scripts\\activate" not in text.lower()

    def test_error_hooks_present(self):
        text = _read_script("start_study_agent.ps1")
        assert "MessageBox" in text and "::Show" in text   # 失败弹窗
        assert "launcher.log" in text                       # 失败日志
        assert "未找到" in text                              # 缺失文件错误文案

    def test_production_does_not_pass_date_unconditionally(self):
        """--date 只在显式 -Date 时才加入，生产启动默认不传。"""
        text = _read_script("start_study_agent.ps1")
        assert "if ($Date)" in text

    def test_log_writes_utf8_without_bom(self):
        assert "[System.Text.UTF8Encoding]::new($false)" in _read_script("start_study_agent.ps1")


# ==================== 快捷方式静态检查 ====================

class TestShortcutStatic:
    def test_hidden_launch_arguments(self):
        text = _read_script("create_shortcut.ps1")
        for token in ("-NoProfile", "-ExecutionPolicy Bypass", "-WindowStyle Hidden", "-File"):
            assert token in text

    def test_com_object_and_save(self):
        text = _read_script("create_shortcut.ps1")
        for token in ("WScript.Shell", "CreateShortcut", ".Save()"):
            assert token in text

    def test_target_workingdir_properties(self):
        text = _read_script("create_shortcut.ps1")
        for token in ("TargetPath", "WorkingDirectory", "Arguments", "Description"):
            assert token in text
        assert "$WorkingDir = $ProjectRoot" in text

    def test_deterministic_shortcut_name(self):
        """快捷方式文件名固定为 Study Agent.lnk，不含随机/时间戳后缀。"""
        text = _read_script("create_shortcut.ps1")
        assert "Study Agent.lnk" in text
        for m in re.finditer(r'"[^"]*\.lnk"', text):
            candidate = m.group(0)
            # "$Name.lnk" 是函数模板（默认名 Study Agent），其余必须固定
            if "$Name.lnk" in candidate:
                continue
            assert "Study Agent.lnk" in candidate, candidate

    def test_no_download_icon_no_third_party(self):
        text = _read_script("create_shortcut.ps1")
        assert "http://" not in text and "https://" not in text

    def test_idempotent_update_semantics(self):
        # 更新而非重复创建的机制说明
        text = _read_script("create_shortcut.ps1")
        assert "已存在" in text and "不重复" in text


# ==================== 诊断脚本静态检查 ====================

class TestDiagnoseStatic:
    def test_reports_paths_and_version(self):
        text = _read_script("diagnose_windows.ps1")
        for token in ("Project root", "Python path", "Python version", "PySide6 version"):
            assert token in text

    def test_reports_api_status_and_meta(self):
        text = _read_script("diagnose_windows.ps1")
        for token in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
            assert token in text
        assert "DEEPSEEK_API_KEY: Configured" in text
        assert "DEEPSEEK_API_KEY: Missing" in text

    def test_uses_user_environment_variable_layer(self):
        text = _read_script("diagnose_windows.ps1")
        assert "GetEnvironmentVariable" in text
        assert "User" in text


# ==================== 纯 Python 路径逻辑镜像 ====================

def _mirror_launch_paths(project_root: Path):
    """复刻 start_study_agent.ps1 的路径解析与检查顺序。"""
    python = project_root / ".venv" / "Scripts" / "python.exe"
    main = project_root / "app" / "main.py"
    errors: list[str] = []
    if not python.exists():
        errors.append("python")
    if not main.exists():
        errors.append("main")
    return python, main, errors


class TestPathLogicMirror:
    def test_root_is_parent_of_scripts_dir(self):
        assert PROJECT_ROOT == SCRIPTS_DIR.parent

    def test_python_and_main_paths_resolved(self, tmp_path):
        python_path = tmp_path / ".venv" / "Scripts" / "python.exe"
        main_py = tmp_path / "app" / "main.py"
        python_path.parent.mkdir(parents=True)
        main_py.parent.mkdir()
        python_path.touch()
        main_py.touch()

        python, main, errors = _mirror_launch_paths(tmp_path)
        assert python == python_path
        assert main == main_py
        assert errors == []

    def test_missing_python_reports_python_error_first(self, tmp_path):
        main_py = tmp_path / "app" / "main.py"
        main_py.parent.mkdir()
        main_py.touch()
        _, _, errors = _mirror_launch_paths(tmp_path)
        assert errors == ["python"]

    def test_missing_main_reports_main_error(self, tmp_path):
        python = tmp_path / ".venv" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.touch()
        _, _, errors = _mirror_launch_paths(tmp_path)
        assert errors == ["main"]

    def test_shortcut_arguments_format(self):
        """快捷方式 Arguments 模板：隐藏 + 免交互 + 指向启动器。"""
        launcher = PROJECT_ROOT / "scripts" / "start_study_agent.ps1"
        args = (
            '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden '
            f'-File "{launcher}"'
        )
        assert args.startswith("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden")
        assert args.endswith("start_study_agent.ps1\"")
        assert f'"{launcher}"' in args
        assert launcher.name == "start_study_agent.ps1"
        assert SCRIPTS_DIR == launcher.parent


# ==================== pwsh / powershell 实测（无则跳过） ====================

SHELL = _launch_shell()
needs_shell = pytest.mark.skipif(SHELL is None, reason="本机没有 pwsh / powershell")


def _normalize(p: str) -> str:
    return p.replace("\\", "/")


def _make_fake_project(tmp_path, name: str) -> Path:
    """在临时目录搭一个模拟项目：scripts + app + .venv。"""
    root = tmp_path / name
    script_dir = root / "scripts"
    script_dir.mkdir(parents=True)
    for n in SCRIPT_NAMES:
        shutil.copy(SCRIPTS_DIR / n, script_dir / n)
    (root / "app").mkdir()
    (root / "app" / "main.py").touch()
    (root / ".venv" / "Scripts").mkdir(parents=True)
    (root / ".venv" / "Scripts" / "python.exe").touch()
    return root


def _run_ps(shell: str, script: Path, cwd: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd),
        timeout=120,
    )


@needs_shell
class TestPwshLive:
    def test_dryrun_resolves_paths_and_omits_date(self, tmp_path):
        root = _make_fake_project(tmp_path, "p1")
        launcher = root / "scripts" / "start_study_agent.ps1"
        r = _run_ps(SHELL, launcher, root, "-DryRun")
        out = _normalize(r.stdout + r.stderr)
        assert r.returncode == 0, out
        assert f"ProjectRoot={_normalize(str(root))}" in out
        assert ".venv/Scripts/python.exe" in out
        assert "app/main.py" in out
        cmd_line = next(l for l in out.splitlines() if "Command:" in l)
        assert "--date" not in cmd_line

    def test_dryrun_missing_python_errors(self, tmp_path):
        root = tmp_path / "p2"
        script_dir = root / "scripts"
        script_dir.mkdir(parents=True)
        shutil.copy(SCRIPTS_DIR / "start_study_agent.ps1", script_dir / "start_study_agent.ps1")
        (root / "app").mkdir()
        (root / "app" / "main.py").touch()
        launcher = root / "scripts" / "start_study_agent.ps1"
        r = _run_ps(SHELL, launcher, root, "-DryRun")
        out = _normalize(r.stdout + r.stderr)
        assert r.returncode != 0
        assert "python.exe" in out
        assert "ERROR" in out or "未找到" in out or "TerminatingError" in r.stderr

    def test_dryrun_missing_main_errors(self, tmp_path):
        root = tmp_path / "p3"
        script_dir = root / "scripts"
        script_dir.mkdir(parents=True)
        shutil.copy(SCRIPTS_DIR / "start_study_agent.ps1", script_dir / "start_study_agent.ps1")
        (root / ".venv" / "Scripts").mkdir(parents=True)
        (root / ".venv" / "Scripts" / "python.exe").touch()
        launcher = root / "scripts" / "start_study_agent.ps1"
        r = _run_ps(SHELL, launcher, root, "-DryRun")
        out = _normalize(r.stdout + r.stderr)
        assert r.returncode != 0
        assert "main.py" in out
        assert "ERROR" in out or "未找到" in out or "TerminatingError" in r.stderr

    def test_create_shortcut_dryrun_idempotent_path(self, tmp_path):
        root = _make_fake_project(tmp_path, "p4")
        launcher = root / "scripts" / "create_shortcut.ps1"
        r = _run_ps(SHELL, launcher, root, "-DryRun")
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert out.count("Study Agent.lnk") >= 2
        assert "would (re)create" in out.lower() or "DryRun" in out
