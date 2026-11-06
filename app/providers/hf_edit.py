"""AI product-scene generation from a reference photo, via free Hugging Face Spaces
(FLUX.1 Kontext / Qwen-Image-Edit, run on HF's free ZeroGPU).

The model looks at the real product photo and re-creates it in a new setting / angle,
removing hands, stickers and watermarks. Free but rate-limited: anonymous users get a small
daily GPU quota; a free HF account token (HF_TOKEN in .env) gives more.
"""
from __future__ import annotations

import threading
from pathlib import Path

from PIL import ImageFilter

from .. import usage
from ..config import AI_REF_SIZE, HF_EDIT_SPACES, HF_KONTEXT_STEPS, HF_TOKEN, REALESRGAN_PATH
from ..utils.imaging import load_any
from .base import ProviderError, QuotaExceeded

_clients: dict[str, object] = {}
_lock = threading.Lock()

# Per-space call signature (parameter names differ between Spaces).
_ARGS = {
    "kontext": lambda img, prompt, seed: dict(input_image=img, prompt=prompt, seed=seed, randomize_seed=False,
                                              guidance_scale=2.5, steps=HF_KONTEXT_STEPS),
    "qwen": lambda img, prompt, seed: dict(image=img, prompt=prompt, seed=seed, randomize_seed=False,
                                           true_guidance_scale=1.0, num_inference_steps=8, rewrite_prompt=False),
}


def _kind(space: str) -> str:
    return "qwen" if "qwen" in space.lower() else "kontext"


def _client(space: str):
    with _lock:
        if space not in _clients:
            from gradio_client import Client
            # long HTTP timeout: a generation takes ~30-90s (+ queue), default timeout cuts it off
            _clients[space] = Client(space, token=HF_TOKEN or None, verbose=False, httpx_kwargs={"timeout": 300})
        return _clients[space]


def _result_path(res) -> str:
    """Spaces return (image, seed) / a dict / a gallery list — dig out the file path."""
    item = res[0] if isinstance(res, (list, tuple)) else res
    if isinstance(item, (list, tuple)) and item:
        item = item[0]
    if isinstance(item, dict):
        item = item.get("image") or item.get("path") or item.get("url")
        if isinstance(item, dict):
            item = item.get("path") or item.get("url")
    if not item or not Path(str(item)).exists():
        raise ProviderError(f"Space không trả về ảnh: {str(res)[:120]}")
    return str(item)


class HFProductScene:
    name = "hf_edit"

    def generate(self, ref_image: Path, prompt: str, seed: int, out_path: Path) -> Path:
        if not usage.within_limit("hf_edit"):
            raise QuotaExceeded("Đã đạt HF_EDIT_DAILY_LIMIT hôm nay")
        from gradio_client import handle_file
        # Send a modest-size reference: faster upload on slow connections, same result.
        ref = load_any(ref_image).convert("RGB")
        ref.thumbnail((AI_REF_SIZE, AI_REF_SIZE))
        tmp = out_path.with_name(out_path.stem + "_ref.jpg")
        ref.save(tmp, quality=92)
        errors = []
        try:
            for space in HF_EDIT_SPACES:
                try:
                    job = _client(space).submit(**_ARGS[_kind(space)](handle_file(str(tmp)), prompt, seed),
                                                api_name="/infer")
                    res = job.result(timeout=420)  # includes waiting in the Space queue
                    img = load_any(_result_path(res)).convert("RGB")
                    img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=2))
                    img.save(out_path, quality=94)
                    usage.log("hf_edit", "product_scene", True, f"{space}: {prompt[:80]}")
                    return out_path
                except Exception as e:  # gradio raises AppError / httpx errors
                    msg = str(e)
                    usage.log("hf_edit", "product_scene", False, f"{space}: {msg[:150]}")
                    if "quota" in msg.lower() or "429" in msg:
                        errors.append(f"{space}: hết quota GPU miễn phí")
                    else:
                        errors.append(f"{space}: {msg[:80] or type(e).__name__}")
        finally:
            tmp.unlink(missing_ok=True)
        if errors and all("quota" in e for e in errors):
            raise QuotaExceeded("; ".join(errors) + (" — quota GPU miễn phí tự hồi lại sau ~24 giờ" if HF_TOKEN
                                                 else " — thêm HF_TOKEN (tài khoản free) để có thêm quota"))
        raise ProviderError("; ".join(errors))


# ---------------------------------------------------------------- prompt building
# Camera framing only — HOW the product is supported/placed comes from `staging` (product-specific).
ANGLES = {
    "intro": "eye-level hero shot, the product clearly visible and large in frame",
    "closeup": "close-up shot that shows the details, texture and print of the product",
    "context": "natural lifestyle shot of the product being used in everyday life",
    "benefit": "three-quarter view rotated only 15-30 degrees, the same side of the product still facing the camera",
    "cta": "clean, simple composition from a slightly elevated angle",
}

DEFAULT_STAGING = "placed the way it is normally displayed or used, resting naturally on a surface"

_PRODUCT_LOCK = ("Keep the product's shape, proportions, colors, printed design, logo and text exactly identical "
                 "to the reference; never show a side of the product that is not in the reference. ")
_REALISM = ("The placement must be physically realistic: the product is properly supported by a surface, a device "
            "or a hand, never floating or standing upright in a way it could not stand on its own. ")
# Subtitles and the CTA button sit in the lower part of the video.
_LAYOUT = "Vertical 9:16 frame; the product sits in the upper-middle, the bottom quarter is calm, uncluttered background. "
_FINISH = "No added text, stickers or watermarks. Photorealistic, soft natural light, shallow depth of field."
_SAME_BG = ("the same background: same wall, furniture, plants and props in the same places, same colors, "
            "same light direction and color grading")


def _hands(handheld: bool) -> str:
    if handheld:
        return ("If it fits the shot, one natural, well-formed hand (five fingers) may hold or use the product; "
                "replace the hands from the reference with that new hand. ")
    return "No hands or people. "


def scene_prompt(visual: str, setting: str, staging: str = "", handheld: bool = False, props: str = "") -> str:
    """ANCHOR shot: made from the real product photo. It fixes the look (device, hand, background, light)
    that every other shot of the video copies."""
    staging = staging or DEFAULT_STAGING
    return (f"Professional vertical product photo of the exact product shown in the reference image, {staging}. "
            + (f"Scene setup: {props}. " if props else "")
            + f"Location: {setting or 'a clean minimal studio'}. Shot: {ANGLES.get(visual, ANGLES['benefit'])}. "
            + _LAYOUT + _REALISM + _hands(handheld) + _PRODUCT_LOCK
            + "Remove stickers, price tags, watermarks and text overlays from the reference. " + _FINISH)


# Camera change only — used on top of the anchor shot.
FOLLOW_ANGLES = {
    "intro": "move the camera slightly so the product is large and centered",
    "closeup": "move the camera closer for a close-up of the product's details, texture and print",
    "context": "step back a little for a wider shot of the same setup",
    "benefit": "orbit the camera 15-30 degrees around the product (same side still facing the camera)",
    "cta": "a slightly elevated angle with a clean, simple composition",
}


def _same(props: str, handheld: bool, setting: str) -> list[str]:
    same = ["the same product with identical shape, colors, printed design, logo and text"]
    if props:
        same.append(f"the same {props}")
    if handheld:
        same.append("the same hand with the same skin tone, nails and sleeve (natural, five fingers)")
    if not setting:
        same.append(_SAME_BG)
    return same


def follow_prompt(visual: str, props: str = "", handheld: bool = False, setting: str = "") -> str:
    """FOLLOW shot (single reference = the anchor shot): only the camera changes."""
    place = (f"Move the whole setup to this location: {setting}, keeping the same light direction. "
             if setting else "")
    return (f"Change only the camera: {FOLLOW_ANGLES.get(visual, FOLLOW_ANGLES['benefit'])}. {place}"
            f"Keep everything else exactly as in the reference image: {'; '.join(_same(props, handheld, setting))}. "
            "Do not add, remove, replace or recolor any object. " + _LAYOUT + _REALISM + _FINISH)


def pro_prompt(visual: str, n_product: int, has_scene: bool, setting: str, staging: str = "",
               handheld: bool = False, props: str = "") -> str:
    """Multi-reference models (Nano Banana / Seedream): product photos + (optionally) the anchor scene."""
    refs = (f"Reference images 1-{n_product} show the exact product (real photos)." if n_product > 1
            else "Reference image 1 shows the exact product (real photo).")
    if has_scene:
        refs += (f" Reference image {n_product + 1} is the scene of this video: recreate that scene with "
                 f"{'; '.join(_same(props, handheld, ''))}. Only the camera changes: "
                 f"{FOLLOW_ANGLES.get(visual, FOLLOW_ANGLES['benefit'])}.")
        body = "Do not add, remove, replace or recolor any object. "
    else:
        body = (f"Show it {staging or DEFAULT_STAGING}. " + (f"Scene setup: {props}. " if props else "")
                + f"Location: {setting or 'a clean minimal studio'}. Shot: {ANGLES.get(visual, ANGLES['benefit'])}. "
                + _hands(handheld))
    return (f"Professional vertical product photo. {refs} {body}" + _LAYOUT + _REALISM + _PRODUCT_LOCK
            + "Ignore stickers, price tags, watermarks and text overlays in the product photos. " + _FINISH)


def upscale(src: Path) -> Path:
    """Real-ESRGAN x4 (local, GPU via Vulkan) when REALESRGAN_PATH is set; cached next to the image."""
    if not REALESRGAN_PATH or not Path(REALESRGAN_PATH).exists():
        return src
    out = src.with_name(src.stem + "_x4.png")
    if out.exists():
        return out
    import subprocess
    try:
        subprocess.run([REALESRGAN_PATH, "-i", str(src), "-o", str(out), "-s", "4", "-n", "realesrgan-x4plus"],
                       capture_output=True, timeout=180, check=True,
                       cwd=str(Path(REALESRGAN_PATH).parent))
        return out if out.exists() else src
    except Exception:
        return src


def cover_frame(src: Path, out_path: Path, width: int, height: int) -> Path:
    """Fill the 9:16 motion canvas with the generated photo."""
    from ..utils.imaging import cover
    img = load_any(upscale(src)).convert("RGB")
    cover(img, width, height).save(out_path, quality=93)
    return out_path
