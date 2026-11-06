"""Prompt templates for the LLM provider.

Token budget notes (keep it this way):
- Instructions are in English (Vietnamese costs ~1.5-2x more tokens); output values are requested in Vietnamese.
- Schemas are compact one-line JSON, no indentation.
- Only the fields a step actually needs are sent (e.g. scripts never see image boxes).
- Outputs have hard caps on list sizes / sentence lengths and no duplicated fields.
"""
from __future__ import annotations

import json
from datetime import date

UNKNOWN = "chưa xác định"

CATEGORIES = {
    "cosmetics": "Mỹ phẩm / làm đẹp",
    "household": "Đồ gia dụng",
    "fashion": "Quần áo / thời trang",
    "accessory": "Phụ kiện",
    "food": "Thực phẩm / đồ uống",
    "electronics": "Đồ điện tử / công nghệ",
    "other": "Khác",
}

STYLES = {
    "review": "honest user review, friendly first-person",
    "ad": "short clear ad, energetic but not exaggerated",
    "cinematic": "cinematic, visual wording, slow elegant pace",
    "lifestyle": "lifestyle, product inside daily routine, warm and casual",
}

VISUALS = ["intro", "closeup", "context", "benefit", "cta"]


def _j(obj) -> str:
    """Compact JSON (no spaces/indent) — the cheapest way to show a schema or data."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


ANALYSIS_SCHEMA = {
    "product_name": "", "category": "vi", "category_key": "|".join(CATEGORIES),
    "colors": [""], "shape": "", "material": "", "visible_text": [""], "key_features": [""],
    "target_customers": [""], "unknowns": [""], "staging": "", "handheld": False, "props": "",
    "images": [{"index": 0, "role": "front|back|side|detail|packaging|in_use|lifestyle|infographic|size_chart|other",
                "product_box": [0, 0, 0, 0], "has_hand": False, "hand_boxes": [[0, 0, 0, 0]],
                "has_text_overlay": False, "background": "plain|studio|scene|busy", "quality": "good|ok|poor",
                "usable": True, "note": ""}],
    "best_main_image": 0,
}


def analysis_prompt(info: dict) -> str:
    return f"""Analyze the product in the {info.get('n_images', '')} labelled images ("Ảnh N" = index N) for a TikTok affiliate video.
Seller info (trusted): name={info.get('name') or '?'}; price={info.get('price') or '?'}; category={info.get('category') or 'auto'}; uses={info.get('user_info') or '?'}
Rules:
- Describe only what is visible or given by the seller. Never invent uses, ingredients, certifications, origin.
- visible_text: exact text printed ON the product/packaging only; skip blurry text.
- Photos may come from Shopee/TikTok shops: ignore added overlays (shop logos, prices, "Freeship", stickers, watermarks, frames) — they are not product info.
- Main product = the item repeated across images / matching the seller name.
- images: one entry per image. product_box=[ymin,xmin,ymax,xmax] normalized 0-1000, tight around the main product only (exclude hands, people, props, overlays); null if absent. hand_boxes for hands/people touching it. usable=false for size charts, text-only banners, collages of other products.
- staging: ENGLISH, ≤25 words, how this product is realistically shown in a photo (what supports it, how it is used), e.g. phone case -> "fitted on an iPhone, lying on a desk or held in one hand"; serum bottle -> "standing upright on a shelf"; T-shirt -> "worn by a person or laid flat". handheld=true if it is normally held in the hand when used/shown.
- props: ENGLISH, ≤30 words, ONE fixed look reused in every shot of the video so all shots match: the device/body it is fitted on or used with (model + exact color as seen in the photos), the hand if handheld (gender, skin tone, nails, sleeve). No location/surface (chosen per video). Do not describe the product itself. Empty if nothing goes with it.
- Unknown values: "{UNKNOWN}" and list them in unknowns.
- All free-text values (except staging, props) in Vietnamese with full diacritics; keep enum values as given.
Return only JSON: {_j(ANALYSIS_SCHEMA)}"""


TREND_SCHEMA = {
    "summary": "", "trending_formats": [{"name": "", "how": "", "why": "", "src": [1]}],
    "hook_patterns": [{"pattern": "", "example": "", "src": [1]}], "pacing": "", "music_notes": "",
    "hashtags": ["#"], "top_videos": [{"title": "", "url": "", "views": 0, "takeaway": "", "src": 1}],
    "avoid": [""], "recommendations": [""], "data_quality": "cao|trung bình|thấp",
}

# What the summariser and the script writer need from the analysis — nothing else.
_FACT_KEYS = ("product_name", "category", "colors", "shape", "material", "visible_text", "key_features",
              "target_customers")


def product_facts(analysis: dict) -> dict:
    return {k: analysis[k] for k in _FACT_KEYS if analysis.get(k) not in (None, "", [], UNKNOWN, [UNKNOWN])}


def _source_line(s: dict) -> str:
    head = [f"[{s['id']}]{s['type'][0].upper()}"]  # A=article V=video R=reference
    if s.get("platform"):
        head.append(s["platform"])
    if s.get("views"):
        head.append(f"{s['views']}views")
    if s.get("published"):
        head.append(s["published"])
    body = (s.get("text") or s.get("snippet") or "").strip()
    body = body[:900] if s.get("text") else body[:220]
    return f"{' '.join(head)} | {s.get('title', '')[:120]}" + (f" | {body}" if body else "")


def trend_prompt(analysis: dict, info: dict, sources: list[dict]) -> str:
    return f"""Today {date.today():%Y-%m-%d}. Summarize CURRENT TikTok affiliate trends in Vietnam for making a video of:
{info.get('name') or analysis.get('product_name')} ({analysis.get('category')}). Known product uses: {', '.join(analysis.get('key_features') or []) or '?'}.
Sources (A=article, V=video with views, R=reference video sent by the user — most important):
{chr(10).join(_source_line(s) for s in sources)}
Rules:
- Use only these sources; cite ids in src. No invented numbers or videos. Prefer recent sources and high-view videos.
- Trends = HOW to present (hook, format, pacing, angle). Never attribute new features/ingredients to our product; examples must not add product claims.
- Our video = product photos + motion + AI voice (no person on camera); recommendations must fit that.
- top_videos only from V/R sources (≤4). hashtags only those seen in sources or very common ones (≤8).
- Max 3 formats, 3 hooks, 3 avoid, 4 recommendations; each string ≤25 words. Say so in data_quality/summary if data is thin or old.
- All text in Vietnamese with full diacritics.
Return only JSON: {_j(TREND_SCHEMA)}"""


def _trend_block(trends: dict | None) -> str:
    if not trends:
        return "none — write from general TikTok experience; do NOT claim anything is trending"
    r = trends.get("report", trends)
    slim = {
        "summary": r.get("summary"),
        "formats": [f"{f.get('name')}: {f.get('how') or f.get('description', '')}" for f in r.get("trending_formats") or []],
        "hooks": [h.get("pattern") for h in r.get("hook_patterns") or []],
        "pacing": r.get("pacing"),
        "top_videos": [f"{v.get('title', '')[:80]} ({v.get('views') or '?'} views): {v.get('takeaway', '')}"
                       for v in (r.get("top_videos") or [])[:4]],
        "avoid": r.get("avoid"), "tips": r.get("recommendations"),
    }
    return _j({k: v for k, v in slim.items() if v})[:2500]


SCRIPT_SCHEMA = {"scripts": [{"title": "", "trend_used": "", "comparison": "", "caption": "", "hashtags": ["#"],
                              "scenes": [{"text": "", "visual": "|".join(VISUALS), "background_prompt": ""}]}]}


def script_prompt(analysis: dict, info: dict, style: str, duration: int, count: int,
                  trends: dict | None = None) -> str:
    syllables = int(duration * 3.6)
    n_scenes = 4 if duration <= 15 else (5 if duration <= 30 else 6)
    return f"""Write {count} DIFFERENT Vietnamese TikTok affiliate video scripts (different angle and hook each).
Product facts: {_j(product_facts(analysis))}
Seller: name={info.get('name') or analysis.get('product_name') or '?'}; price={info.get('price') or 'không nêu'}; uses={info.get('user_info') or 'không có'}
Trends: {_trend_block(trends)}
Rules:
- Style: {STYLES.get(style, STYLES['review'])}. Natural spoken Vietnamese with FULL diacritics, short sentences, not salesy.
- Exactly {n_scenes} scenes; total narration ≤{syllables} syllables (~{duration}s). Scene 1 = hook (grab attention in 3s), last = CTA (tap the cart / link). Narration = scene texts in order.
- Facts only from the product facts and seller info. No exaggeration, promises, medical claims, invented uses/ingredients; never mention unknown items.
- One product only: no "top 3", "best/đáng mua nhất", no comparison with other products. Banned words: quốc dân, thần thánh, số 1, tốt nhất, 100%, cam kết, hiệu quả ngay, chữa, trị, đánh bay, vĩnh viễn.
- Apply a different trend format/hook per script when available; learn the pattern, never copy other videos' wording. trend_used and comparison: one short Vietnamese sentence each.
- No emoji or symbols in text (it is read by TTS). visual: first intro, last cta.
- background_prompt: English, ≤15 words, the location/setting that fits the product and scene (e.g. "cozy cafe table by the window"); do not describe the product; no text.
- caption ≤150 characters; hashtags ≤6.
Return only JSON: {_j(SCRIPT_SCHEMA)}"""


def staging_prompt(analysis: dict, info: dict) -> str:
    """Tiny call (+1 photo) for products analysed before `staging`/`props` existed."""
    return f"""Product: {info.get('name') or analysis.get('product_name')} ({analysis.get('category')}). Facts: {_j(product_facts(analysis))}
How is this product realistically shown in a product photo (what supports it / how it is used)? English, ≤25 words.
Examples: phone case -> "fitted on an iPhone, lying on a desk or held in one hand"; serum -> "standing upright on a shelf".
handheld=true if it is normally held in the hand when used or shown.
props: ENGLISH, ≤30 words, ONE fixed look reused in every shot of the video so all shots match: the device/body it is fitted on or used with (model + exact color as seen in the photos), the hand if handheld (gender, skin tone, nails, sleeve). No location/surface (chosen per video). Do not describe the product itself. Empty if nothing goes with it. Use the attached photo for colors.
Return only JSON: {_j({"staging": "", "handheld": False, "props": ""})}"""


def verify_prompt(has_scene: bool) -> str:
    """Gemini judges an AI product photo (images: A = real product, [B = anchor scene], last = candidate)."""
    scene = ("B = the first shot of this video. scene: 0-10, are the device, hand (skin, nails, sleeve), "
             "background objects and light the same as in B? " if has_scene else "")
    return f"""A = real product photo. {scene}Last image = AI-generated shot.
Score 0-10, strict. product: same shape, colors, printed design, logo and text as A (ignore A's stickers/watermarks/hands).
{scene and "scene: as above. "}real: physically plausible (no floating product, hands have 5 normal fingers, no melted parts, no added text).
issues: ≤15 English words.
Return only JSON: {_j({"product": 0, "scene": 0, "real": 0, "issues": ""} if has_scene else {"product": 0, "real": 0, "issues": ""})}"""


# Used when no LLM is available.
STAGING_BY_CATEGORY = {
    "cosmetics": ("standing upright on a vanity or bathroom shelf", False),
    "household": ("placed where it is used at home, resting on a counter or table", False),
    "fashion": ("worn naturally or laid flat neatly", False),
    "accessory": ("used or worn the way it is normally used, resting naturally or held in one hand", True),
    "food": ("served or placed on a table, package resting naturally", False),
    "electronics": ("resting flat on a desk or held in one hand while in use", True),
    "other": ("placed the way it is normally displayed or used, resting naturally on a surface", False),
}


BG_SUFFIX = (", empty clean surface in the center foreground with space for a product, no product, "
             "no people, no text, no logo, no watermark, photorealistic, soft natural light, "
             "shallow depth of field, vertical composition")
