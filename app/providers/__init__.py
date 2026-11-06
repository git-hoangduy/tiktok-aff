"""Provider registry. Add a new vendor here to make it selectable."""
from __future__ import annotations

from ..config import BG_PROVIDERS
from .base import ImageProvider, LLMProvider, ProviderError, QuotaExceeded, TTSProvider  # noqa: F401
from .gemini import GeminiImage, GeminiLLM, available as gemini_available
from .images import PollinationsImage, ProceduralImage
from .template_llm import TemplateLLM
from .tts import ChainTTS, EdgeTTS, GeminiTTS, GoogleTranslateTTS

_IMAGE = {"pollinations": PollinationsImage, "gemini_image": GeminiImage, "procedural": ProceduralImage}


def llm_chain() -> list[LLMProvider]:
    chain: list[LLMProvider] = []
    if gemini_available():
        chain.append(GeminiLLM())
    chain.append(TemplateLLM())
    return chain


def image_chain(local_only: bool = False) -> list[ImageProvider]:
    names = ["procedural"] if local_only else BG_PROVIDERS
    return [_IMAGE[n]() for n in names if n in _IMAGE]


_TTS = {"gemini_tts": GeminiTTS, "gtts": GoogleTranslateTTS, "edge_tts": EdgeTTS}


def tts() -> ChainTTS:
    from ..config import TTS_PROVIDERS
    names = [n for n in TTS_PROVIDERS if n in _TTS] or ["gtts", "edge_tts"]
    return ChainTTS([_TTS[n]() for n in names])
