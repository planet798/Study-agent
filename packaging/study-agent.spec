"""Windows onedir bundle. Only explicitly listed public resources enter it."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VSVersionInfo,
    VarFileInfo, VarStruct,
)

root = Path(SPECPATH).parent
version = (root / "build/version.txt").read_text().strip()
version_tuple = tuple(int(part) for part in version.split(".")) + (0,)
info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_tuple, prodvers=version_tuple, mask=0x3f,
                     flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("ProductName", "Study Agent"),
        StringStruct("FileDescription", "Study Agent 桌面学习助手"),
        StringStruct("FileVersion", version), StringStruct("ProductVersion", version),
        StringStruct("OriginalFilename", "StudyAgent.exe"),
    ])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])],
)
datas = [
    (str(root / "app/ui/design/icons"), "app/ui/design/icons"),
    (str(root / "app/ui/design/styles"), "app/ui/design/styles"),
    (str(root / "assets"), "assets"),
    (str(root / "oauth_bridge/index.mjs"), "oauth_bridge"),
    (str(root / "oauth_bridge/request-options.mjs"), "oauth_bridge"),
    (str(root / "oauth_bridge/node_modules"), "oauth_bridge/node_modules"),
    (str(root / "build/runtime"), "runtime"),
    (str(root / "build/licenses"), "licenses"),
]
for package in ("keyring", "mcp", "mcp-types"):
    datas += copy_metadata(package, recursive=True)
analysis = Analysis(
    [str(root / "app/desktop_entry.py")], pathex=[str(root)],
    binaries=[], datas=datas,
    hiddenimports=collect_submodules("keyring.backends") + ["win32timezone"],
    excludes=["pytest", "pytestqt", "tkinter", "PySide6.QtWebEngineCore",
              "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="StudyAgent",
          console=False, debug=False, upx=False, version=info,
          icon=str(root / "assets/study-agent.ico"))
bundle = COLLECT(exe, analysis.binaries, analysis.datas, strip=False,
                 upx=False, name="StudyAgent")
