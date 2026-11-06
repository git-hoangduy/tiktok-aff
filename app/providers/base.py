"""Provider interfaces. Swap any AI vendor by implementing one of these."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class ProviderError(Exception):
    """Provider failed; caller should fall back to the next provider."""


class QuotaExceeded(ProviderError):
    """Out of free quota / credit, or the user's daily limit was reached."""


class LLMProvider(ABC):
    name = "llm"

    @abstractmethod
    def analyze_product(self, images: list[Path], info: dict) -> dict:
        """Return the product analysis dict (see prompts.ANALYSIS_SCHEMA)."""

    @abstractmethod
    def generate_scripts(self, analysis: dict, info: dict, style: str, duration: int, count: int,
                         trends: dict | None = None) -> list[dict]:
        """Return `count` scripts (see prompts.SCRIPT_SCHEMA), informed by the trend report."""

    def summarize_trends(self, analysis: dict, info: dict, sources: list[dict]) -> dict:
        """Turn collected trend sources into a report (see prompts.TREND_SCHEMA)."""
        raise ProviderError(f"{self.name} không hỗ trợ tóm tắt xu hướng")


class ImageProvider(ABC):
    name = "image"
    is_remote = True

    @abstractmethod
    def generate(self, prompt: str, width: int, height: int, seed: int, out_path: Path, context: dict) -> Path:
        """Create a background image at out_path. `context` carries product colors, category, etc."""


class TTSProvider(ABC):
    name = "tts"

    @abstractmethod
    def synthesize(self, text: str, voice: str, rate: int, out_path: Path) -> Path:
        """Write speech audio for `text`. `rate` is a percentage (+10 = 10% faster)."""
