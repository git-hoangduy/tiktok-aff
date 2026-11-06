"""Text-to-speech providers."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from .. import usage
from .base import ProviderError, TTSProvider


class EdgeTTS(TTSProvider):
    """Microsoft Edge online voices via the `edge-tts` package (free, no key).
    Note: it uses an unofficial endpoint and may change without notice."""
    name = "edge_tts"

    def synthesize(self, text: str, voice: str, rate: int, out_path: Path) -> Path:
        import edge_tts

        rate_s = f"{'+' if rate >= 0 else ''}{int(rate)}%"
        last_err: Exception | None = None
        # The service is occasionally flaky ("No audio was received") — retry with backoff.
        for attempt in range(6):
            try:
                async def run():
                    await edge_tts.Communicate(text, voice, rate=rate_s).save(str(out_path))
                asyncio.run(run())
                if out_path.exists() and out_path.stat().st_size > 1000:
                    usage.log("edge_tts", "tts", True, f"{voice} {len(text)} chars"
                              + (f" (lần thử {attempt + 1})" if attempt else ""))
                    return out_path
                raise ProviderError("File âm thanh rỗng")
            except Exception as e:
                last_err = e
                time.sleep(min(8.0, 1.0 + 1.5 * attempt))
        usage.log("edge_tts", "tts", False, str(last_err)[:200])
        raise ProviderError(f"Edge TTS lỗi (cần Internet): {last_err}")


class GoogleTranslateTTS(TTSProvider):
    """Fallback: Google Translate voice via `gTTS` (free, no key, one Vietnamese voice).
    Speed is adjusted afterwards with FFmpeg because gTTS has no rate control."""
    name = "gtts"

    def synthesize(self, text: str, voice: str, rate: int, out_path: Path) -> Path:
        if "NamMinh" in voice or voice.lower() == "male":
            raise ProviderError("gTTS chỉ có giọng nữ")  # male voice -> next engine (Edge NamMinh)
        from gtts import gTTS

        from ..utils import ffmpeg
        raw = out_path.with_suffix(".gtts.mp3")
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                gTTS(text=text, lang="vi").save(str(raw))
                tempo = max(0.8, min(1.6, 1.18 * (1 + rate / 100)))  # gTTS speaks slowly
                ffmpeg.run(["-i", raw, "-af", f"atempo={tempo:.3f}", "-c:a", "libmp3lame", "-q:a", "3", out_path])
                raw.unlink(missing_ok=True)
                usage.log("gtts", "tts", True, f"{len(text)} chars")
                return out_path
            except Exception as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        usage.log("gtts", "tts", False, str(last_err)[:200])
        raise ProviderError(f"gTTS lỗi: {last_err}")


class GeminiTTS(TTSProvider):
    """Gemini prebuilt voices (e.g. Leda) with the same free GEMINI_API_KEY. The free tier allows only a
    few requests per minute/day: per-minute limits are waited out, a daily limit hands over to the next engine."""
    name = "gemini_tts"
    _off_until: str = ""  # date (YYYY-MM-DD) the daily quota ran out

    def synthesize(self, text: str, voice: str, rate: int, out_path: Path) -> Path:
        import datetime as dt
        import re

        from ..config import GEMINI_API_KEY, GEMINI_TTS_MODEL, GEMINI_TTS_VOICES, VOICES
        from .base import QuotaExceeded
        gender = next((g for g, v in VOICES.items() if voice in (g, v)), "female")
        name = GEMINI_TTS_VOICES.get(gender) or ""
        if not (GEMINI_API_KEY and GEMINI_TTS_MODEL and name):
            raise ProviderError(f"Gemini TTS chưa bật cho giọng {gender}")
        today = dt.date.today().isoformat()
        if GeminiTTS._off_until == today:
            raise QuotaExceeded("Gemini TTS đã hết hạn mức hôm nay")
        if not usage.within_limit("gemini_tts"):
            raise QuotaExceeded("Đã đạt GEMINI_TTS_DAILY_LIMIT hôm nay")
        from google.genai import types

        from ..utils import ffmpeg
        from .gemini import _get_client
        cfg = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=name))))
        last_err: Exception | None = None
        for attempt in range(4):
            try:
                resp = _get_client().models.generate_content(
                    model=GEMINI_TTS_MODEL, contents="Đọc tự nhiên, vui vẻ như review TikTok: " + text, config=cfg)
                part = resp.candidates[0].content.parts[0].inline_data
                if not part or not part.data:
                    raise ProviderError("Gemini không trả về âm thanh")
                sr = re.search(r"rate=(\d+)", part.mime_type or "")
                raw = out_path.with_suffix(".pcm")
                raw.write_bytes(part.data)
                tempo = max(1.0, min(1.4, 1 + (rate - 5) / 100))  # already brisk; only speed up when too long
                ffmpeg.run(["-f", "s16le", "-ar", sr.group(1) if sr else "24000", "-ac", "1", "-i", raw,
                            "-af", f"atempo={tempo:.3f}", "-c:a", "libmp3lame", "-q:a", "3", out_path])
                raw.unlink(missing_ok=True)
                usage.log("gemini_tts", "tts", True, f"{GEMINI_TTS_MODEL} {name} {len(text)} chars")
                return out_path
            except Exception as e:
                last_err, msg = e, str(e)
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    if "PerDay" in msg or "per day" in msg.lower():
                        GeminiTTS._off_until = today
                        usage.log("gemini_tts", "tts", False, "hết hạn mức ngày")
                        raise QuotaExceeded("Gemini TTS hết hạn mức miễn phí hôm nay") from e
                    wait = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+)", msg)
                    time.sleep(min(60, int(wait.group(1)) + 2) if wait else 20 * (attempt + 1))
                    continue
                if any(c in msg for c in ("500", "503", "UNAVAILABLE")):
                    time.sleep(3 * (attempt + 1))
                    continue
                break
        usage.log("gemini_tts", "tts", False, str(last_err)[:200])
        raise ProviderError(f"Gemini TTS lỗi: {str(last_err)[:200]}")


class ChainTTS(TTSProvider):
    """Try providers in order. Remembers the last provider used so a voice
    stays consistent inside one video when possible."""
    name = "chain"

    def __init__(self, providers: list[TTSProvider]):
        self.providers = providers
        self.used: list[str] = []  # engine used for each call, in order

    def synthesize(self, text: str, voice: str, rate: int, out_path: Path) -> Path:
        errors = []
        for p in self.providers:
            try:
                p.synthesize(text, voice, rate, out_path)
                self.used.append(p.name)
                return out_path
            except ProviderError as e:
                errors.append(f"{p.name}: {e}")
        raise ProviderError("Không tạo được giọng đọc: " + "; ".join(errors))
