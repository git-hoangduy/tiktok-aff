"""Shared Pillow helpers."""
from __future__ import annotations

import colorsys
import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFile, ImageFont, ImageOps

from ..config import FONT_PATH

_VI_COLORS = [
    ((245, 245, 245), "trắng"), ((20, 20, 20), "đen"), ((128, 128, 128), "xám"),
    ((200, 30, 40), "đỏ"), ((240, 130, 40), "cam"), ((240, 210, 60), "vàng"),
    ((60, 160, 70), "xanh lá"), ((40, 90, 200), "xanh dương"), ((120, 200, 230), "xanh nhạt"),
    ((130, 60, 170), "tím"), ((240, 150, 180), "hồng"), ((140, 90, 50), "nâu"),
    ((225, 205, 170), "be"), ((200, 170, 90), "vàng đồng"), ((0, 128, 128), "xanh ngọc"),
]


# Photos copied from Shopee/TikTok are often truncated or oddly encoded — be tolerant.
ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = 120_000_000

# Optional decoders: HEIC/HEIF (iPhone) via pillow-heif, AVIF via pillow-avif-plugin (newer Pillow has AVIF built in).
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass
try:
    import pillow_avif  # noqa: F401
except ImportError:
    pass

# .jfif/.jpe are plain JPEG; Pillow detects formats from the bytes, not the extension.
ACCEPT_EXT = (".jpg", ".jpeg", ".jfif", ".jpe", ".pjpeg", ".pjp", ".png", ".webp", ".avif", ".heic", ".heif",
              ".gif", ".bmp", ".tif", ".tiff")


def load_any(src) -> Image.Image:
    """Open any supported image (path or bytes) -> upright RGB or RGBA image.
    Handles EXIF rotation, CMYK, 16-bit, palette transparency and animated files (first frame)."""
    img = Image.open(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src)
    if getattr(img, "is_animated", False):
        img.seek(0)
    img = ImageOps.exif_transpose(img)
    mode = img.mode
    if mode in ("I;16", "I;16B", "I;16L", "I"):
        img = img.convert("I").point(lambda x: x * (1 / 256)).convert("L")  # 16-bit -> 8-bit
        mode = "L"
    if mode == "P":
        img = img.convert("RGBA" if "transparency" in img.info else "RGB")
    elif mode in ("LA", "PA", "RGBa", "La"):
        img = img.convert("RGBA")
    elif mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")  # CMYK, YCbCr, L, 1, F ...
    if img.mode == "RGBA" and img.split()[-1].getextrema()[0] >= 250:
        img = img.convert("RGB")  # alpha channel present but fully opaque
    return img


def open_rgb(path: Path | str) -> Image.Image:
    img = load_any(path)
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def box_to_pixels(box, w: int, h: int, margin: float = 0.0) -> tuple[int, int, int, int] | None:
    """Gemini box [ymin, xmin, ymax, xmax] normalised 0-1000 -> pixel (left, top, right, bottom)."""
    try:
        y0, x0, y1, x1 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    if not (0 <= y0 < y1 <= 1000 and 0 <= x0 < x1 <= 1000):
        return None
    if (y1 - y0) * (x1 - x0) < 400:  # < 0.04% of the image: not a real detection
        return None
    mx, my = (x1 - x0) * margin, (y1 - y0) * margin
    left, right = max(0.0, x0 - mx) * w / 1000, min(1000.0, x1 + mx) * w / 1000
    top, bottom = max(0.0, y0 - my) * h / 1000, min(1000.0, y1 + my) * h / 1000
    return int(left), int(top), int(round(right)), int(round(bottom))


def dominant_colors(img: Image.Image, n: int = 4) -> list[tuple[int, int, int]]:
    """Dominant colours, ignoring transparent and near-white background pixels."""
    rgba = img.convert("RGBA")
    rgba.thumbnail((160, 160))
    px = [p[:3] for p in rgba.getdata() if p[3] > 128 and not (p[0] > 235 and p[1] > 235 and p[2] > 235)]
    if not px:
        px = [p[:3] for p in rgba.getdata()] or [(200, 200, 200)]
    small = Image.new("RGB", (len(px), 1))
    small.putdata(px)
    q = small.quantize(colors=n, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[: n * 3]
    counts = sorted(q.getcolors(), reverse=True)
    return [tuple(pal[i * 3: i * 3 + 3]) for _, i in counts if i < n]


def color_name(rgb: tuple[int, int, int]) -> str:
    return min(_VI_COLORS, key=lambda c: sum((a - b) ** 2 for a, b in zip(c[0], rgb)))[1]


def adjust(rgb, light: float | None = None, sat: float | None = None):
    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    if light is not None:
        l = light
    if sat is not None:
        s = min(1.0, s * sat)
    return tuple(int(c * 255) for c in colorsys.hls_to_rgb(h, l, s))


@lru_cache(maxsize=32)
def font(size: int) -> ImageFont.FreeTypeFont:
    if FONT_PATH:
        return ImageFont.truetype(FONT_PATH, size)
    return ImageFont.load_default(size)


def wrap(text: str, fnt, max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=fnt) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    return ImageOps.fit(img, (w, h), Image.Resampling.LANCZOS)
