"""ProductAnalyzer: images + user info -> structured, non-invented product facts."""
from __future__ import annotations

import re
from pathlib import Path

from .. import providers
from ..prompts import CATEGORIES, UNKNOWN


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    return [str(x).strip() for x in v if str(x).strip()]


ROLES = {"front", "back", "side", "detail", "packaging", "in_use", "lifestyle", "infographic", "size_chart", "other"}


def _box(v):
    if isinstance(v, str):
        v = re.findall(r"-?\d+(?:\.\d+)?", v)
    if isinstance(v, (list, tuple)) and len(v) == 4:
        try:
            b = [max(0.0, min(1000.0, float(x))) for x in v]
        except (TypeError, ValueError):
            return None
        if b[0] < b[2] and b[1] < b[3]:
            return b
    return None


def _bool(v) -> bool:
    return v is True or str(v).strip().lower() in ("true", "1", "yes", "có")


def normalize_images(raw, n: int) -> list[dict]:
    """One entry per uploaded image (index 0..n-1), whatever the model returned."""
    by_idx: dict[int, dict] = {}
    for k, it in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(it, dict):
            continue
        try:
            idx = int(it.get("index", k))
        except (TypeError, ValueError):
            idx = k
        if 0 <= idx < n and idx not in by_idx:
            by_idx[idx] = it
    out = []
    for i in range(n):
        it = by_idx.get(i, {})
        role = str(it.get("role", "other")).strip().lower()
        out.append({
            "index": i,
            "role": role if role in ROLES else "other",
            "product_box": _box(it.get("product_box")),
            "has_hand": _bool(it.get("has_hand")),
            "hand_boxes": [b for b in (_box(x) for x in (it.get("hand_boxes") or [])) if b],
            "has_text_overlay": _bool(it.get("has_text_overlay")),
            "background": str(it.get("background", "")).strip().lower() or "unknown",
            "quality": str(it.get("quality", "")).strip().lower() or "unknown",
            "usable": it.get("usable") is None or _bool(it.get("usable")),
            "note": str(it.get("note", ""))[:200],
            "inspected": bool(it),
        })
    return out


def normalize(a: dict, info: dict, n_images: int = 0) -> dict:
    out = {
        "product_name": str(a.get("product_name") or info.get("name") or UNKNOWN).strip(),
        "category": str(a.get("category") or UNKNOWN).strip(),
        "category_key": a.get("category_key") if a.get("category_key") in CATEGORIES else "other",
        "colors": _as_list(a.get("colors")) or [UNKNOWN],
        "shape": str(a.get("shape") or UNKNOWN),
        "material": str(a.get("material") or UNKNOWN),
        "visible_text": _as_list(a.get("visible_text")),
        "key_features": _as_list(a.get("key_features")),
        "target_customers": _as_list(a.get("target_customers")) or [UNKNOWN],
        "unknowns": _as_list(a.get("unknowns")),
        "notes": str(a.get("notes") or ""),
        "staging": str(a.get("staging") or "").strip()[:200],
        "handheld": _bool(a.get("handheld")),
        "props": str(a.get("props") or "").strip().rstrip(".")[:300],
        "images": normalize_images(a.get("images"), n_images),
    }
    try:
        best = int(a.get("best_main_image", 0))
    except (TypeError, ValueError):
        best = 0
    usable = [im["index"] for im in out["images"] if im["usable"]]
    if best not in usable:
        best = usable[0] if usable else 0
    out["best_main_image"] = best
    # The user's own name and category always win over the AI's guess.
    if info.get("name"):
        out["product_name"] = info["name"].strip()
    if info.get("category") in CATEGORIES:
        out["category_key"] = info["category"]
        if out["category"] == UNKNOWN:
            out["category"] = CATEGORIES[info["category"]]
    return out


class ProductAnalyzer:
    def analyze(self, images: list[Path], info: dict) -> tuple[dict, str, list[str]]:
        warnings: list[str] = []
        for p in providers.llm_chain():
            try:
                return normalize(p.analyze_product(images, info), info, len(images)), p.name, warnings
            except providers.ProviderError as e:
                warnings.append(f"{p.name}: {e}")
        raise RuntimeError("Không phân tích được sản phẩm: " + "; ".join(warnings))
