"""独立 onefile 辅助程序；在安装目录外运行，不引入 Qt/模型依赖。"""
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VSVersionInfo, VarFileInfo, VarStruct,
)

root = Path(SPECPATH).parent
version = (root / "build/version.txt").read_text().strip()
parts = tuple(int(value) for value in version.split(".")) + (0,)
info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=parts, prodvers=parts, mask=0x3f, flags=0,
                     OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("ProductName", "Study Agent"),
        StringStruct("FileDescription", "Study Agent 升级辅助程序"),
        StringStruct("FileVersion", version), StringStruct("ProductVersion", version),
        StringStruct("OriginalFilename", "StudyAgentUpdater.exe"),
    ])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])],
)
analysis = Analysis(
    [str(root / "app/updates/updater.py")], pathex=[str(root)],
    binaries=[], datas=[], hiddenimports=[],
    excludes=["PySide6", "mcp", "keyring", "pytest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, analysis.binaries, analysis.datas,
          name="StudyAgentUpdater", console=False, debug=False, upx=False, version=info,
          icon=str(root / "assets/study-agent.ico"))
