"""收集实际运行依赖的许可；不扫描用户目录和开发环境数据。"""
from importlib import metadata
from pathlib import Path
import json
import shutil
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    target = root / "build/licenses"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    packages = []
    for line in (root / "packaging/windows-runtime.txt").read_text().splitlines():
        if "==" not in line:
            continue
        name, version = line.split("==")
        dist = metadata.distribution(name)
        if dist.version != version:
            raise RuntimeError(f"Runtime lock mismatch: {name}")
        folder = target / "python" / name
        folder.mkdir(parents=True, exist_ok=True)
        for file in dist.files or []:
            if any(word in str(file).upper() for word in ("LICENSE", "COPYING", "COPYRIGHT", "NOTICE")):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    relative = Path(*file.parts)
                    if ".." in relative.parts:
                        continue
                    output = folder / relative
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, output)
        packages.append({"name": name, "version": version,
                         "license": dist.metadata.get("License-Expression") or dist.metadata.get("License", "See bundled license files")})
    bootloader = metadata.distribution("pyinstaller")
    for file in bootloader.files or []:
        if any(word in str(file).upper() for word in ("LICENSE", "COPYING", "COPYRIGHT", "NOTICE")):
            source = Path(bootloader.locate_file(file))
            if source.is_file() and ".." not in file.parts:
                output = target / "pyinstaller" / Path(*file.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, output)
    shutil.copytree(root / "packaging/licenses/qt", target / "qt")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.exists():
        raise RuntimeError("Python license missing")
    shutil.copy2(python_license, target / "PYTHON-LICENSE.txt")
    node_root = root / "oauth_bridge/node_modules"
    for source in node_root.rglob("*"):
        if source.is_file() and source.name.upper().startswith(("LICENSE", "COPYING", "COPYRIGHT", "NOTICE")):
            output = target / "node" / source.relative_to(node_root)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, output)
    shutil.copy2(root / "app/ui/design/icons/LICENSE", target / "FLUENT-ICONS-LICENSE.txt")
    shutil.copy2(root / "build/runtime/LICENSE", target / "NODE-LICENSE.txt")
    shutil.copy2(root / "oauth_bridge/package-lock.json", target / "node-package-lock.json")
    (target / "python-packages.json").write_text(json.dumps(packages, indent=2, ensure_ascii=False), encoding="utf-8")
    (target / "README.txt").write_text(
        "Study Agent bundles Python, PySide6/Qt (LGPL/commercial), Node.js and the dependencies listed here.\n"
        "Individual copyright and license terms are included in the subdirectories.\n"
        "The executable bootloader is provided by PyInstaller; its license and exception are included.\n"
        "Python dependencies: https://pypi.org/ ; Qt source: https://download.qt.io/official_releases/qt/\n"
        "Node.js source: https://nodejs.org/dist/ ; OAuth bridge: see node-package-lock.json\n"
        "The Qt libraries remain separate shared libraries in _internal/PySide6 and may be replaced\n"
        "with compatible versions under their license terms.\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()
