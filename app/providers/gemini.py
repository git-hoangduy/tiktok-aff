"""Google Gemini (free tier via Google AI Studio key)."""
from __future__ import annotations

import io
import json
import re
import time
from pathlib import Path

from PIL import Image

from .. import usage
from ..config import (GEMINI_API_KEY, GEMINI_FALLBACK_MODELS, GEMINI_IMAGE_MODEL, GEMINI_MODEL,
                      GEMINI_VERIFY_MODEL)
from ..prompts import analysis_prompt, script_prompt, staging_prompt, trend_prompt, verify_prompt
from ..utils.imaging import open_rgb
from .base import ImageProvider, LLMProvider, ProviderError, QuotaExceeded

_client = None


def available() -> bool:
    return bool(GEMINI_API_KEY)


def _get_client():
    global _client
    if _client is None:
        from google import genai
        from google.genai import types
        # 120s cap per request so an overloaded model can't hang the job forever
        _client = genai.Client(api_key=GEMINI_API_KEY, http_options=types.HttpOptions(timeout=120_000))
    return _client


def _classify(exc: Exception) -> ProviderError:
    msg = str(exc)
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
        return QuotaExceeded(f"Gemini hết hạn mức miễn phí hoặc bị giới hạn tốc độ: {msg[:200]}")
    return ProviderError(f"Gemini lỗi: {msg[:300]}")


def _parse_json(text: str):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def _image_part(path: Path):
    from google.genai import types
    img = open_rgb(path)  # same orientation as the stored file, so boxes map 1:1
    img.thumbnail((768, 768))  # ~1 image tile = fewest tokens; aspect kept so normalised boxes stay valid
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")


# Output caps per task: generous enough for valid JSON, small enough to stop rambling.
MAX_OUT = {"analyze": 2500, "trends": 2000, "scripts": 4000, "staging": 300, "verify": 200}
# Thinking adds hidden (counted) tokens; these tasks don't need it. Which option a model accepts
# differs per generation, so we try them in order and remember what worked.
# "plain" = also drop media_resolution, in case a model rejects that field.
_THINKING_OPTIONS = [{"thinking_level": "low"}, {"thinking_budget": 0}, None, "plain"]
_thinking_ok: dict[str, int] = {}


def _tokens(resp) -> tuple[int, int]:
    um = getattr(resp, "usage_metadata", None)
    if not um:
        return 0, 0
    out = (um.candidates_token_count or 0) + (getattr(um, "thoughts_token_count", 0) or 0)
    return um.prompt_token_count or 0, out


class GeminiLLM(LLMProvider):
    name = "gemini"

    def _generate(self, model: str, contents, kind: str):
        from google.genai import types
        start = _thinking_ok.get(model, 0)
        for k in range(start, len(_THINKING_OPTIONS)):
            opt = _THINKING_OPTIONS[k]
            cfg = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.8 if kind == "scripts" else 0.2,
                max_output_tokens=MAX_OUT.get(kind, 2500),
                thinking_config=types.ThinkingConfig(**opt) if isinstance(opt, dict) else None,
                # product photos: medium resolution is enough to read labels and locate the product
                media_resolution=(types.MediaResolution.MEDIA_RESOLUTION_MEDIUM
                                  if kind == "analyze" and opt != "plain" else None),
            )
            try:
                resp = _get_client().models.generate_content(model=model, contents=contents, config=cfg)
                _thinking_ok[model] = k
                return resp
            except Exception as e:
                msg = str(e)
                # 400 caused by an unsupported thinking option -> try the next option on the same model
                if opt != "plain" and "400" in msg and ("thinking" in msg.lower() or "INVALID_ARGUMENT" in msg):
                    continue
                raise
        raise ProviderError(f"{model}: không gọi được với cấu hình nào")

    def _call(self, contents, kind: str, validate=None):
        """validate(data) -> error message or None. An invalid answer moves on to the next model."""
        if not available():
            raise ProviderError("Chưa có GEMINI_API_KEY trong .env")
        if not usage.within_limit("gemini"):
            raise QuotaExceeded("Đã đạt GEMINI_DAILY_LIMIT bạn đặt trong .env cho hôm nay")
        last: ProviderError | None = None
        # Try the main model, then fallbacks. Retry briefly on "overloaded" (503/500) errors.
        models = [GEMINI_MODEL, *[m for m in GEMINI_FALLBACK_MODELS if m != GEMINI_MODEL]]
        if kind == "verify" and GEMINI_VERIFY_MODEL:  # simple yes/no judging: the light model is enough & faster
            models = [GEMINI_VERIFY_MODEL, *[m for m in models if m != GEMINI_VERIFY_MODEL]]
        for model in models:
            for attempt in range(3):
                try:
                    resp = self._generate(model, contents, kind)
                    tok_in, tok_out = _tokens(resp)
                    data = _parse_json(resp.text)
                    problem = validate(data) if validate else None
                    if problem:
                        usage.log("gemini", kind, False, f"{model}: {problem}", tokens_in=tok_in, tokens_out=tok_out)
                        last = ProviderError(problem)
                        break
                    usage.log("gemini", kind, True, model, tokens_in=tok_in, tokens_out=tok_out)
                    return data
                except (json.JSONDecodeError, ValueError) as e:
                    usage.log("gemini", kind, False, f"{model}: JSON không hợp lệ: {e}")
                    last = ProviderError(f"Gemini trả về JSON không hợp lệ: {e}")
                    break
                except Exception as e:  # SDK raises various error classes
                    err = _classify(e)
                    usage.log("gemini", kind, False, f"{model}: {err}")
                    if isinstance(err, QuotaExceeded) and "402" in str(e):
                        raise err from e  # project billing problem: other models won't help
                    last = err
                    msg = str(e)
                    if ("503" in msg or "500" in msg or "UNAVAILABLE" in msg) and attempt < 2:
                        time.sleep(3 * (attempt + 1))
                        continue
                    break
        raise last or ProviderError("Gemini lỗi không rõ")

    def analyze_product(self, images: list[Path], info: dict) -> dict:
        parts = []
        for i, p in enumerate(images[:10]):
            parts += [f"Ảnh {i}:", _image_part(p)]  # labels so per-image answers map to the right file
        parts.append(analysis_prompt({**info, "n_images": len(images[:10])}))
        data = self._call(parts, "analyze")
        if not isinstance(data, dict):
            raise ProviderError("Kết quả phân tích không đúng định dạng")
        return data

    def staging(self, analysis: dict, info: dict, image: Path | None = None) -> dict:
        def validate(data):
            return None if isinstance(data, dict) and str(data.get("staging", "")).strip() else "thiếu staging"
        parts = ([_image_part(image)] if image else []) + [staging_prompt(analysis, info)]
        return self._call(parts, "staging", validate)

    def verify_image(self, product: Path, candidate: Path, scene: Path | None = None) -> dict:
        def validate(data):
            return None if isinstance(data, dict) and "product" in data else "thiếu điểm"
        parts = ["A:", _image_part(product)]
        if scene:
            parts += ["B:", _image_part(scene)]
        parts += ["Last image:", _image_part(candidate), verify_prompt(bool(scene))]
        return self._call(parts, "verify", validate)

    def summarize_trends(self, analysis: dict, info: dict, sources: list[dict]) -> dict:
        def validate(data):
            if not isinstance(data, dict) or not data.get("summary"):
                return "báo cáo xu hướng rỗng"
            if _vi_diacritic_ratio(str(data.get("summary"))) < 0.08:
                return "báo cáo xu hướng bị mất dấu"
            return None
        return self._call([trend_prompt(analysis, info, sources)], "trends", validate)

    def generate_scripts(self, analysis: dict, info: dict, style: str, duration: int, count: int,
                         trends: dict | None = None) -> list[dict]:
        def extract(data):
            return data.get("scripts") if isinstance(data, dict) else data

        def validate(data):
            scripts = extract(data)
            if not isinstance(scripts, list) or not scripts:
                return "không trả về kịch bản"
            text = " ".join(str(sc.get("text", "")) for s in scripts if isinstance(s, dict)
                            for sc in (s.get("scenes") or []) if isinstance(sc, dict))
            if not text.strip():
                return "kịch bản không có lời thoại"
            if _vi_diacritic_ratio(text) < 0.12:
                return "kịch bản tiếng Việt bị mất dấu"
            return None

        data = self._call([script_prompt(analysis, info, style, duration, count, trends)], "scripts", validate)
        return extract(data)[:count]


def _vi_diacritic_ratio(text: str) -> float:
    """Share of letters that are non-ASCII. Normal Vietnamese is ~25-35%; unaccented text is ~0%."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if ord(c) > 127) / len(letters)


class GeminiImage(ImageProvider):
    """Gemini native image generation. Disabled unless GEMINI_IMAGE_MODEL is set."""
    name = "gemini_image"

    def generate(self, prompt, width, height, seed, out_path, context):
        if not (available() and GEMINI_IMAGE_MODEL):
            raise ProviderError("Gemini image chưa bật (GEMINI_IMAGE_MODEL trống)")
        if not usage.within_limit("gemini_image"):
            raise QuotaExceeded("Đã đạt GEMINI_IMAGE_DAILY_LIMIT hôm nay")
        from google.genai import types
        try:
            cfg = types.GenerateContentConfig(response_modalities=["IMAGE"])
            try:
                cfg.image_config = types.ImageConfig(aspect_ratio="9:16")
            except Exception:
                pass
            resp = _get_client().models.generate_content(
                model=GEMINI_IMAGE_MODEL, contents=[prompt + ". Vertical 9:16."], config=cfg)
            for cand in resp.candidates or []:
                for part in (cand.content.parts if cand.content else []) or []:
                    if getattr(part, "inline_data", None) and part.inline_data.data:
                        Image.open(io.BytesIO(part.inline_data.data)).convert("RGB").save(out_path, quality=92)
                        usage.log("gemini_image", "background", True, GEMINI_IMAGE_MODEL)
                        return out_path
            raise ProviderError("Gemini không trả về ảnh")
        except ProviderError as e:
            usage.log("gemini_image", "background", False, str(e))
            raise
        except Exception as e:
            err = _classify(e)
            usage.log("gemini_image", "background", False, str(err))
            raise err from e
