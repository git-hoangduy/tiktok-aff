"""Offline fallback 'LLM'. Works without any API key, never invents claims:
it only uses what the user typed plus colours measured from the photos."""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

from ..prompts import CATEGORIES, UNKNOWN
from ..utils.imaging import color_name, dominant_colors
from .base import LLMProvider

BG = {
    "cosmetics": ["elegant vanity table with marble top, soft morning window light, blurred bedroom",
                  "clean bathroom counter with plants and soft daylight",
                  "pastel pink studio backdrop with silk fabric and soft shadows"],
    "household": ["modern bright kitchen countertop, wooden texture, morning sunlight",
                  "cozy living room coffee table, warm light, minimal scandinavian interior",
                  "clean white kitchen shelf with plants, natural light"],
    "fashion": ["minimal fashion studio with beige backdrop and soft spotlight",
                "boutique clothing store interior, warm lighting, blurred racks",
                "aesthetic bedroom with wooden floor and soft window light"],
    "accessory": ["minimal wooden work desk with laptop blurred in background, soft light",
                  "lifestyle cafe table with coffee cup blurred, warm tones",
                  "modern display podium, gradient backdrop, studio lighting"],
    "food": ["rustic wooden dining table, warm kitchen light, blurred background",
             "bright picnic table outdoors, soft sunlight, bokeh",
             "clean marble kitchen counter, natural daylight"],
    "electronics": ["modern dark desk setup with soft neon rim light, blurred monitor",
                    "minimal white desk, clean tech workspace, daylight",
                    "sleek studio podium with blue gradient lighting"],
    "other": ["minimal studio podium with soft gradient backdrop",
              "cozy home interior table, natural light, blurred background",
              "clean lifestyle shelf with plants, soft daylight"],
}


def _benefit_lines(user_info: str) -> list[str]:
    # split on lines, bullets and sentences — but not on hyphens inside words/numbers ("30-50ml")
    parts = re.split(r"\n|;|•|(?:^|\s)[-–+*]\s+|\.\s+", user_info or "")
    return [p.strip(" .,") for p in parts if len(p.strip()) > 3][:4]


class TemplateLLM(LLMProvider):
    name = "template"

    def analyze_product(self, images: list[Path], info: dict) -> dict:
        colors: list[str] = []
        for p in images[:3]:
            try:
                for c in dominant_colors(Image.open(p), 3):
                    n = color_name(c)
                    if n not in colors:
                        colors.append(n)
            except Exception:
                pass
        cat = info.get("category") if info.get("category") in CATEGORIES else "other"
        name = info.get("name") or UNKNOWN
        unknowns = ["chất liệu", "thành phần", "kích thước", "khách hàng mục tiêu"]
        if not info.get("user_info"):
            unknowns.append("công dụng")
        return {
            "product_name": name,
            "category": CATEGORIES[cat],
            "category_key": cat,
            "colors": colors[:4] or [UNKNOWN],
            "shape": UNKNOWN,
            "material": UNKNOWN,
            "visible_text": [],
            "key_features": _benefit_lines(info.get("user_info", "")),
            "target_customers": [UNKNOWN],
            "unknowns": unknowns,
            "notes": "Phân tích offline (không có AI): chỉ dùng thông tin bạn nhập và màu sắc đo từ ảnh.",
        }

    def generate_scripts(self, analysis: dict, info: dict, style: str, duration: int, count: int,
                         trends: dict | None = None) -> list[dict]:
        report = (trends or {}).get("report", {})
        tags = report.get("hashtags") or []
        scripts = self._scripts(analysis, info, duration, count)
        for s in scripts:
            s["trend_used"] = "không dựa trên trend (kịch bản mẫu offline)"
            s["comparison"] = ""
            s["caption"] = s["hook"]
            s["hashtags"] = (tags[:6] or []) + ["#tiktokshop", "#review"]
        return scripts

    def _scripts(self, analysis: dict, info: dict, duration: int, count: int) -> list[dict]:
        name = info.get("name") or analysis.get("product_name") or "sản phẩm này"
        if name == UNKNOWN:
            name = "sản phẩm này"
        cat = analysis.get("category_key", "other")
        bgs = BG.get(cat, BG["other"])
        color = next((c for c in analysis.get("colors", []) if c != UNKNOWN), "")
        benefits = _benefit_lines(info.get("user_info", "")) or analysis.get("key_features", [])
        if not benefits:
            benefits = [f"thiết kế {'màu ' + color + ' ' if color else ''}nhìn rất gọn gàng, dễ dùng"]
        price = (info.get("price") or "").strip()
        price_line = f" Giá đang là {price}." if price else ""

        n_scenes = 4 if duration <= 15 else (5 if duration <= 30 else 6)
        noun = {"cosmetics": "mỹ phẩm", "household": "đồ gia dụng", "fashion": "đồ mặc đẹp",
                "accessory": "phụ kiện", "food": "đồ ăn ngon", "electronics": "đồ công nghệ"}.get(cat, "món đồ hay")
        hooks = [
            f"Ai đang tìm {noun} thì dừng lại xem cái này nè.",
            f"Mình vừa tìm được {name}, xem cận cảnh với mình nhé.",
            f"Đây là {name}, món mà nhiều người đang hỏi mình nè.",
        ]
        intros = [
            f"Đây là {name}.",
            f"Nhìn cận cảnh thì {name} trông như thế này.",
            f"Mình giới thiệu nhanh {name} cho mọi người nha.",
        ]
        ctas = [
            f"Link sản phẩm mình để ở giỏ hàng, bấm vào xem chi tiết nhé.{price_line}",
            f"Bạn nào quan tâm thì bấm giỏ hàng bên dưới để xem thêm nha.{price_line}",
            f"Xem giá và đặt hàng ở giỏ hàng góc dưới nhé.{price_line}",
        ]
        scripts = []
        for i in range(count):
            mid_needed = n_scenes - 3
            mids = [benefits[(i + k) % len(benefits)] for k in range(mid_needed)]
            mids = [m[0].upper() + m[1:] + "." if m else m for m in mids]
            scenes = [{"text": hooks[i % 3], "visual": "intro", "background_prompt": bgs[i % 3]},
                      {"text": intros[i % 3], "visual": "closeup", "background_prompt": bgs[(i + 1) % 3]}]
            visuals = ["context", "benefit", "context", "benefit"]
            for k, m in enumerate(mids):
                scenes.append({"text": m, "visual": visuals[k], "background_prompt": bgs[(i + k + 2) % 3]})
            scenes.append({"text": ctas[i % 3], "visual": "cta", "background_prompt": bgs[i % 3]})
            scripts.append({
                "title": ["Hook thu hút", "Cận cảnh sản phẩm", "Giới thiệu nhanh"][i % 3],
                "hook": hooks[i % 3], "intro": intros[i % 3],
                "benefits": mids, "cta": ctas[i % 3], "scenes": scenes,
            })
        return scripts
