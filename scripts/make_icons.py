"""生成 PWA 图标（运行一次：python scripts/make_icons.py）。
产物：web/icons/icon-192.png、web/icons/icon-512.png
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "web" / "icons"
OUT.mkdir(parents=True, exist_ok=True)


def draw(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 圆角深色底
    r = size // 5
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=(16, 19, 28, 255))
    # 白色显示器边框
    m = size // 6
    th = max(2, size // 40)
    d.rectangle([m, m, size - m, int(size * 0.62)], outline=(255, 255, 255, 255), width=th)
    # 屏幕内渐变模拟桌面（几条横线）
    for i, y in enumerate(range(int(size * 0.34), int(size * 0.60), int(size * 0.06))):
        col = (43, 109, 246, 255) if i % 2 == 0 else (61, 74, 102, 255)
        d.rectangle([m + size // 10, y, size - m - size // 10, y + max(2, size // 60)], fill=col)
    # 底座 + 支架
    foot_w = size // 3
    d.rectangle([size // 2 - foot_w // 2, int(size * 0.66), size // 2 + foot_w // 2, int(size * 0.72)],
                fill=(255, 255, 255, 255))
    d.rectangle([size // 2 - th, int(size * 0.62), size // 2 + th, int(size * 0.66)], fill=(255, 255, 255, 255))
    # 右上角绿色在线点
    d.ellipse([int(size * 0.72), int(size * 0.12), int(size * 0.84), int(size * 0.24)], fill=(52, 211, 153, 255))
    return img


for s in (192, 512):
    draw(s).save(OUT / f"icon-{s}.png")
    print(f"wrote web/icons/icon-{s}.png")
