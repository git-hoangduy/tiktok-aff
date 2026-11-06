"""Paid Pollinations API (gen.pollinations.ai, needs a secret `sk_` key, billed in pollen).

Only used when the user picks a paid option in the render form ("AI Pro" images, "AI motion" clips)
AND POLLINATIONS_TOKEN is set. Everything here falls back to the free path on any error.
Docs checked 06/11/2026: https://gen.pollinations.ai/openapi.json
"""
from __future__ import annotations

import time
import urllib.parse
from pathlib import Path

import httpx

from .. import usage
from ..config import (POLLINATIONS_EDIT_MODEL, POLLINATIONS_TOKEN, VIDEO_MODELS)
from ..utils.imaging import load_any
from .base import ProviderError, QuotaExceeded

GEN = "https://gen.pollinations.ai"
MEDIA = "https://media.pollinations.ai"

# Estimated pollen per image (from /image/models pricing; token-priced models estimated for ~1K output).
EDIT_PRICES = {"nanobanana2": 0.08, "nanobanana-2": 0.08, "seedream": 0.03, "seedream-pro": 0.042,
               "kontext": 0.03, "nanobanana-pro": 0.16, "gptimage-large": 0.05}


def available() -> bool:
    return POLLINATIONS_TOKEN.startswith("sk_")


def edit_price(model: str = "") -> float:
    return EDIT_PRICES.get(model or POLLINATIONS_EDIT_MODEL, 0.08)


def video_price(model: str, seconds: float) -> float:
    price, lo, hi = VIDEO_MODELS[model]
    return price * max(lo, min(hi, round(seconds)))


def _headers() -> dict:
    return {"Authorization": f"Bearer {POLLINATIONS_TOKEN}", "User-Agent": "aff-tiktok-local/1.0"}


def _error(r: httpx.Response, kind: str) -> ProviderError:
    try:
        err = r.json().get("error", {})
        msg = f"{err.get('code', '')} {err.get('message', '')}".strip()
    except Exception:
        msg = r.text[:150]
    usage.log("pollinations_pro", kind, False, f"HTTP {r.status_code} {msg}"[:200])
    if r.status_code == 402:
        return QuotaExceeded(f"Pollinations hết pollen / hết ngân sách key ({msg[:80]})")
    if r.status_code == 429:
        return QuotaExceeded("Pollinations: gọi quá nhanh (429)")
    return ProviderError(f"Pollinations HTTP {r.status_code}: {msg[:150]}")


def balance() -> float | None:
    if not available():
        return None
    try:
        r = httpx.get(f"{GEN}/account/balance", headers=_headers(), timeout=10)
        return float(r.json()["balance"]) if r.status_code == 200 else None
    except Exception:
        return None


_uploaded: dict[tuple[str, float], str] = {}


def upload(path: Path, max_side: int = 1280) -> str:
    """Reference images must be public URLs: upload once per file version (links expire after 30 days)."""
    key = (str(path), path.stat().st_mtime)
    if key in _uploaded:
        return _uploaded[key]
    img = load_any(path).convert("RGB")
    img.thumbnail((max_side, max_side))
    tmp = path.with_name(path.stem + "_up.jpg")
    img.save(tmp, quality=92)
    try:
        with tmp.open("rb") as f:
            r = httpx.post(f"{MEDIA}/upload", headers=_headers(), params={"safe": "privacy"},
                           files={"file": (tmp.name, f, "image/jpeg")}, timeout=60)
    finally:
        tmp.unlink(missing_ok=True)
    if r.status_code != 200:
        raise _error(r, "upload")
    url = r.json().get("url")
    if not url:
        raise ProviderError("Pollinations upload không trả về URL")
    _uploaded[key] = url
    return url


def _fetch(path: str, params: dict, kind: str, timeout: float) -> bytes:
    """Async submit + poll, so long video jobs don't hit HTTP timeouts. A cached/fast answer comes back directly."""
    h = {**_headers(), "Prefer": "respond-async"}
    r = httpx.get(f"{GEN}{path}", params=params, headers=h, timeout=120, follow_redirects=True)
    if r.status_code == 200 and not r.headers.get("content-type", "").startswith("application/json"):
        return r.content
    if r.status_code != 202:
        raise _error(r, kind)
    job = r.json().get("id") or r.headers.get("location", "").rstrip("/").split("/")[-1]
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(int(r.headers.get("retry-after", 5) or 5))
        r = httpx.get(f"{GEN}/v1/jobs/{job}/result", headers=_headers(), timeout=120, follow_redirects=True)
        ctype = r.headers.get("content-type", "")
        if r.status_code == 200 and not ctype.startswith("application/json"):
            return r.content
        if r.status_code >= 400 and r.status_code not in (404, 409, 425):
            raise _error(r, kind)
        if "fail" in r.text[:500].lower():
            raise ProviderError(f"Pollinations job lỗi: {r.text[:150]}")
    raise ProviderError(f"Pollinations quá {int(timeout)}s chưa xong")


def edit_image(refs: list[Path], prompt: str, out_path: Path, seed: int, model: str = "",
               width: int = 864, height: int = 1536) -> Path:
    """Image editing with several reference images (product photos first, then the scene to match)."""
    if not available():
        raise ProviderError("Chưa có POLLINATIONS_TOKEN (sk_...) trong .env")
    if not usage.within_limit("pollinations_pro"):
        raise QuotaExceeded("Đã đạt POLLINATIONS_PRO_DAILY_LIMIT hôm nay")
    model = model or POLLINATIONS_EDIT_MODEL
    urls = [upload(p) for p in refs]
    # Identical requests are served from a shared cache: the seed text makes retries really new.
    params = {"model": model, "width": width, "height": height, "seed": seed, "image": "|".join(urls)}
    path = "/image/" + urllib.parse.quote(f"{prompt} (v{seed % 1000})"[:1800])
    try:
        data = _fetch(path, params, "image", timeout=300)
    except httpx.HTTPError as e:
        usage.log("pollinations_pro", "image", False, f"network: {e}")
        raise ProviderError(f"Pollinations không kết nối được: {e}") from e
    import io

    from PIL import Image
    try:
        Image.open(io.BytesIO(data)).convert("RGB").save(out_path, quality=94)
    except Exception as e:
        raise ProviderError("Pollinations trả về ảnh hỏng") from e
    usage.log("pollinations_pro", "image", True, f"{model}: {prompt[:80]}", cost=edit_price(model))
    return out_path


def image_to_video(image: Path, prompt: str, seconds: float, model: str, out_path: Path) -> Path:
    """Start frame -> short MP4 clip (9:16). Billed per second."""
    if not available():
        raise ProviderError("Chưa có POLLINATIONS_TOKEN (sk_...) trong .env")
    if model not in VIDEO_MODELS:
        raise ProviderError(f"Model video không hỗ trợ: {model}")
    _, lo, hi = VIDEO_MODELS[model]
    dur = max(lo, min(hi, round(seconds)))
    params = {"model": model, "image": upload(image), "duration": dur, "aspectRatio": "9:16", "audio": "false"}
    try:
        data = _fetch("/video/" + urllib.parse.quote(prompt[:1500]), params, "video", timeout=900)
    except httpx.HTTPError as e:
        usage.log("pollinations_pro", "video", False, f"network: {e}")
        raise ProviderError(f"Pollinations không kết nối được: {e}") from e
    if len(data) < 10_000:
        raise ProviderError("Pollinations trả về video rỗng")
    out_path.write_bytes(data)
    usage.log("pollinations_pro", "video", True, f"{model} {dur}s: {prompt[:60]}", cost=video_price(model, dur))
    return out_path
