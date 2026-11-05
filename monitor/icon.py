"""用程式畫出 App 圖示（工作列 / 系統匣 / exe 共用），不用額外放圖片檔。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RED = (214, 0, 47, 255)
WHITE = (255, 255, 255, 255)


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_icon(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = size * 0.04
    d.rounded_rectangle((pad, pad, size - pad, size - pad), radius=size * 0.22, fill=RED)
    # 日圓符號
    f = _font(int(size * 0.62))
    d.text((size / 2, size / 2 + size * 0.02), "¥", font=f, fill=WHITE, anchor="mm")
    # 右下角放大鏡（監控）
    r = size * 0.15
    cx, cy = size * 0.73, size * 0.73
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=WHITE, width=max(2, int(size * 0.05)), fill=RED)
    w = max(2, int(size * 0.06))
    d.line((cx + r * 0.7, cy + r * 0.7, cx + r * 1.6, cy + r * 1.6), fill=WHITE, width=w)
    return img


def save_ico(path: str | Path) -> None:
    make_icon(256).save(path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    import sys
    save_ico(sys.argv[1] if len(sys.argv) > 1 else "icon.ico")
