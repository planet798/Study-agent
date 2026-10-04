"""从仓库 SVG 生成 Windows 多尺寸 PNG/ICO，无第三方图像依赖。"""
from pathlib import Path
import struct

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main():
    assets = Path(__file__).resolve().parents[1] / "assets"
    renderer = QSvgRenderer(str(assets / "study-agent.svg"))
    entries, blobs = [], []
    sizes = (16, 24, 32, 48, 64, 128, 256)
    offset = 6 + 16 * len(sizes)
    for size in sizes:
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise RuntimeError("PNG encoding failed")
        blob = bytes(data)
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset))
        blobs.append(blob)
        offset += len(blob)
    (assets / "study-agent.ico").write_bytes(struct.pack("<HHH", 0, 1, len(sizes)) + b"".join(entries + blobs))


if __name__ == "__main__":
    main()
