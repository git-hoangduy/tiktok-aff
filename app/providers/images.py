"""Background image providers: Pollinations (free, no key), Gemini image (optional), procedural (local)."""
from __future__ import annotations

import io
import random
import urllib.parse
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter

from .. import usage
from ..config import POLLINATIONS_BG_USE_KEY, POLLINATIONS_MODEL, POLLINATIONS_TOKEN, POLLINATIONS_URL
from ..utils.imaging import adjust, cover, open_rgb
from .base import ImageProvider, ProviderError, QuotaExceeded


class PollinationsImage(ImageProvider):
    name = "pollinations"

    def generate(self, prompt, width, height, seed, out_path, context):
        if not usage.within_limit("pollinations"):
            raise QuotaExceeded("Đã đạt POLLINATIONS_DAILY_LIMIT hôm nay")
        # Request at a moderate size (faster, more likely to be served free) then upscale-crop.
        req_w, req_h = 768, 1344
        keyed = POLLINATIONS_BG_USE_KEY and POLLINATIONS_TOKEN.startswith("sk_")
        base = "https://gen.pollinations.ai/image/{prompt}" if keyed else POLLINATIONS_URL
        url = base.format(prompt=urllib.parse.quote(prompt[:900]))
        params = {"width": req_w, "height": req_h, "seed": seed, "nologo": "true", "private": "true"}
        if POLLINATIONS_MODEL:
            params["model"] = POLLINATIONS_MODEL
        headers = {"User-Agent": "aff-tiktok-local/1.0"}
        if keyed:  # paid (~0.002 pollen) — only when the user turned POLLINATIONS_BG_USE_KEY on
            headers["Authorization"] = f"Bearer {POLLINATIONS_TOKEN}"
        try:
            r = httpx.get(url, params=params, headers=headers, timeout=httpx.Timeout(60, connect=15),
                          follow_redirects=True)
        except httpx.HTTPError as e:
            usage.log("pollinations", "background", False, f"network: {e}")
            raise ProviderError(f"Pollinations không kết nối được: {e}") from e
        ctype = r.headers.get("content-type", "")
        if r.status_code == 429 or r.status_code == 402:
            usage.log("pollinations", "background", False, f"HTTP {r.status_code}")
            raise QuotaExceeded(f"Pollinations giới hạn lượt (HTTP {r.status_code})")
        if r.status_code != 200 or not ctype.startswith("image"):
            usage.log("pollinations", "background", False, f"HTTP {r.status_code} {ctype}")
            raise ProviderError(f"Pollinations lỗi HTTP {r.status_code}: {r.text[:150]}")
        try:
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception as e:
            usage.log("pollinations", "background", False, "ảnh hỏng")
            raise ProviderError("Pollinations trả về ảnh hỏng") from e
        # Anonymous requests may carry a small watermark in the bottom corner — crop it off.
        if not keyed:
            img = img.crop((0, 0, img.width, int(img.height * 0.93)))
        cover(img, width, height).save(out_path, quality=92)
        usage.log("pollinations", "background", True, prompt[:120], cost=0.002 if keyed else 0.0)
        return out_path


class ProceduralImage(ImageProvider):
    """Local backgrounds built from the product's own colours. Always available, zero cost."""
    name = "procedural"
    is_remote = False

    def generate(self, prompt, width, height, seed, out_path, context):
        rnd = random.Random(seed)
        colors = context.get("colors") or [(200, 190, 230)]
        base = colors[rnd.randrange(len(colors))]
        photo = context.get("photo")
        style = rnd.choice(["studio", "studio", "bokeh", "photo"] if photo else ["studio", "bokeh"])

        if style == "photo":
            img = cover(open_rgb(photo), width // 4, height // 4).filter(ImageFilter.GaussianBlur(18))
            img = img.resize((width, height), Image.Resampling.BICUBIC)
            tint = Image.new("RGB", (width, height), adjust(base, light=0.85, sat=0.6))
            img = Image.blend(img, tint, 0.45)
        else:
            top = adjust(base, light=rnd.uniform(0.86, 0.93), sat=0.55)
            bottom = adjust(base, light=rnd.uniform(0.62, 0.74), sat=0.75)
            grad = Image.linear_gradient("L").resize((width, height))
            img = Image.composite(Image.new("RGB", (width, height), bottom),
                                  Image.new("RGB", (width, height), top), grad)
            # floor / table plane
            floor = Image.new("L", (width, height), 0)
            fd = ImageDraw.Draw(floor)
            horizon = int(height * rnd.uniform(0.60, 0.68))
            fd.rectangle([0, horizon, width, height], fill=110)
            floor = floor.filter(ImageFilter.GaussianBlur(40))
            img = Image.composite(Image.new("RGB", (width, height), adjust(base, light=0.95, sat=0.4)), img, floor)
            # spotlight glow behind product
            glow = Image.new("L", (width, height), 0)
            gd = ImageDraw.Draw(glow)
            cx, cy, r = width // 2, int(height * 0.45), int(width * 0.55)
            gd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=150)
            glow = glow.filter(ImageFilter.GaussianBlur(width // 6))
            img = Image.composite(Image.new("RGB", (width, height), (255, 255, 255)), img, glow)

        if style in ("bokeh", "photo"):
            layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            for _ in range(rnd.randint(10, 18)):
                r = rnd.randint(width // 30, width // 9)
                x, y = rnd.randint(0, width), rnd.randint(0, int(height * 0.6))
                c = adjust(colors[rnd.randrange(len(colors))], light=0.92, sat=0.8)
                d.ellipse([x - r, y - r, x + r, y + r], fill=(*c, rnd.randint(40, 90)))
            layer = layer.filter(ImageFilter.GaussianBlur(14))
            img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")

        # vignette
        vig = Image.new("L", (width, height), 120)
        vd = ImageDraw.Draw(vig)
        vd.ellipse([-width * 0.35, -height * 0.2, width * 1.35, height * 1.2], fill=255)
        vig = vig.filter(ImageFilter.GaussianBlur(width // 5))
        img = Image.composite(img, Image.new("RGB", (width, height), adjust(base, light=0.35, sat=0.6)), vig)
        img.save(out_path, quality=92)
        return out_path
