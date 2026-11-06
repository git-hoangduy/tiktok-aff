"""ProductCompositor: place the REAL product pixels onto a generated background.
The product is only scaled and given a shadow — never recoloured or redrawn."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ..utils.imaging import adjust, cover, font, open_rgb, wrap

# layout -> (max width frac, max height frac, anchor y frac, anchor mode)
LAYOUTS = {
    "hero": (0.66, 0.46, 0.46, "center"),
    "closeup": (0.92, 0.58, 0.45, "center"),
    "context": (0.52, 0.36, 0.67, "bottom"),
    "benefit": (0.58, 0.40, 0.53, "center"),
    "cta": (0.58, 0.38, 0.50, "center"),
}


def _card(photo: Path, max_side: int) -> Image.Image:
    """Fallback when background removal is unreliable: the original photo as a framed card."""
    img = open_rgb(photo)
    img.thumbnail((max_side, max_side))
    border = max(10, img.width // 40)
    card = Image.new("RGBA", (img.width + border * 2, img.height + border * 2), (255, 255, 255, 255))
    card.paste(img, (border, border))
    mask = Image.new("L", card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, card.width - 1, card.height - 1],
                                           radius=card.width // 18, fill=255)
    card.putalpha(mask)
    return card


class ProductCompositor:
    def compose(self, bg_path: Path, product_path: Path, is_cutout: bool, layout: str,
                out_path: Path, width: int, height: int) -> Path:
        canvas = cover(open_rgb(bg_path), width, height).convert("RGBA")
        mw, mh, ay, mode = LAYOUTS.get(layout, LAYOUTS["hero"])
        prod = Image.open(product_path).convert("RGBA") if is_cutout else _card(product_path, 1600)

        scale = min(mw * width / prod.width, mh * height / prod.height)
        pw, ph = max(1, int(prod.width * scale)), max(1, int(prod.height * scale))
        prod = prod.resize((pw, ph), Image.Resampling.LANCZOS)
        x = (width - pw) // 2
        y = int(ay * height - (ph if mode == "bottom" else ph / 2))

        # contact shadow (ellipse under the product) + soft drop shadow from its silhouette
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(sh)
        ew, eh = int(pw * 0.85), max(8, int(ph * 0.07))
        d.ellipse([width // 2 - ew // 2, y + ph - eh // 2, width // 2 + ew // 2, y + ph + eh // 2],
                  fill=(0, 0, 0, 120))
        sh = sh.filter(ImageFilter.GaussianBlur(max(6, eh // 2)))
        canvas = Image.alpha_composite(canvas, sh)

        drop = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        silhouette = Image.new("RGBA", prod.size, (0, 0, 0, 0))
        silhouette.putalpha(prod.split()[-1].point(lambda a: int(a * 0.38)))
        drop.paste(silhouette, (x + int(pw * 0.03), y + int(ph * 0.03)), silhouette)
        drop = drop.filter(ImageFilter.GaussianBlur(max(8, pw // 30)))
        canvas = Image.alpha_composite(canvas, drop)

        canvas.alpha_composite(prod, (x, y))
        canvas.convert("RGB").save(out_path, quality=93)
        return out_path

    def photo_frame(self, photo: Path, out_path: Path, width: int, height: int) -> Path:
        """Show a real lifestyle / in-use photo untouched: fitted inside the 9:16 frame over a
        blurred copy of itself (no cropping of the product, no AI redraw)."""
        img = open_rgb(photo)
        bg = cover(img, width // 6, height // 6).filter(ImageFilter.GaussianBlur(6))
        bg = bg.resize((width, height), Image.Resampling.BICUBIC)
        bg = Image.blend(bg, Image.new("RGB", bg.size, (20, 20, 24)), 0.25)
        fg = img.copy()
        fg.thumbnail((int(width * 0.94), int(height * 0.62)), Image.Resampling.LANCZOS)
        if fg.width < width * 0.94 and fg.height < height * 0.62:  # small photo: scale up to fit
            s = min(width * 0.94 / fg.width, height * 0.62 / fg.height)
            fg = fg.resize((int(fg.width * s), int(fg.height * s)), Image.Resampling.LANCZOS)
        mask = Image.new("L", fg.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, fg.width - 1, fg.height - 1], radius=max(12, fg.width // 30),
                                               fill=255)
        x, y = (width - fg.width) // 2, int(height * 0.42 - fg.height / 2)
        shadow = Image.new("L", bg.size, 0)
        shadow.paste(mask, (x + 10, y + 18))
        bg.paste(Image.new("RGB", bg.size, (0, 0, 0)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(24))
                 .point(lambda a: int(a * 0.45)))
        bg.paste(fg, (x, y), mask)
        bg.save(out_path, quality=93)
        return out_path

    # ---------- static text overlays (rendered at output size, not affected by zoom) ----------
    def overlay(self, overlay: dict | None, accent: tuple[int, int, int], price: str,
                out_path: Path, width: int, height: int) -> Path | None:
        if not overlay:
            return None
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        kind, text = overlay.get("kind"), overlay.get("text", "")
        accent_dark = adjust(accent, light=0.38, sat=1.3)
        if kind == "headline":
            f = font(66)
            lines = wrap(text, f, int(width * 0.80), d)[:2]
            lh = 82
            box_h = lh * len(lines) + 60
            top = int(height * 0.11)
            d.rounded_rectangle([60, top, width - 60, top + box_h], radius=36, fill=(255, 255, 255, 235))
            d.rounded_rectangle([60, top, 76, top + box_h], radius=8, fill=(*accent_dark, 255))
            for i, ln in enumerate(lines):
                tw = d.textlength(ln, font=f)
                d.text(((width - tw) / 2, top + 30 + i * lh), ln, font=f, fill=(25, 25, 30, 255))
        elif kind == "badge":
            f = font(56)
            lines = wrap(text, f, int(width * 0.76), d)[:2]
            lh = 70
            box_h = lh * len(lines) + 44
            top = int(height * 0.12)
            d.rounded_rectangle([90, top, width - 90, top + box_h], radius=box_h // 2 if len(lines) == 1 else 40,
                                fill=(*accent_dark, 230))
            for i, ln in enumerate(lines):
                tw = d.textlength(ln, font=f)
                d.text(((width - tw) / 2, top + 22 + i * lh), ln, font=f, fill=(255, 255, 255, 255))
        elif kind == "cta":
            f = font(62)
            top = int(height * 0.10)
            tw = d.textlength(text, font=f)
            bw = int(tw + 120)
            x0 = (width - bw) // 2
            d.rounded_rectangle([x0, top, x0 + bw, top + 116], radius=58, fill=(238, 29, 82, 245))
            d.text(((width - tw) / 2, top + 24), text, font=f, fill=(255, 255, 255, 255))
            if price:
                fp = font(76)
                pt = price.strip()
                pw = d.textlength(pt, font=fp)
                d.rounded_rectangle([(width - pw) / 2 - 40, top + 150, (width + pw) / 2 + 40, top + 270],
                                    radius=30, fill=(255, 255, 255, 240))
                d.text(((width - pw) / 2, top + 165), pt, font=fp, fill=(238, 29, 82, 255))
        img.save(out_path)
        return out_path


def make_glare(out_path: Path, height: int) -> Path:
    """Diagonal light streak used for the 'light sweep' effect."""
    w = 420
    grad = Image.new("L", (w, 1))
    grad.putdata([int(90 * (1 - abs(i - w / 2) / (w / 2)) ** 2) for i in range(w)])
    alpha = grad.resize((w, height))
    streak = Image.new("RGBA", (w, height), (255, 255, 255, 0))
    streak.putalpha(alpha)
    streak = streak.rotate(-14, expand=True, resample=Image.Resampling.BICUBIC)
    streak = ImageOps.fit(streak, (w + 200, height))
    streak.save(out_path)
    return out_path
