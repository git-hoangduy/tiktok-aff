"""ImageGenerator: produce a background image, falling back through providers until one works."""
from __future__ import annotations

from pathlib import Path

from .. import providers


class ImageGenerator:
    def __init__(self) -> None:
        # Providers that ran out of quota / timed out are skipped for the rest of this video,
        # so one dead service doesn't add minutes of waiting to every scene.
        self.disabled: set[str] = set()

    def background(self, prompt: str, seed: int, out_path: Path, context: dict,
                   width: int, height: int, local_only: bool = False) -> tuple[Path, str, list[str]]:
        warnings: list[str] = []
        for p in providers.image_chain(local_only):
            if p.name in self.disabled:
                continue
            try:
                p.generate(prompt, width, height, seed, out_path, context)
                return out_path, p.name, warnings
            except providers.QuotaExceeded as e:
                self.disabled.add(p.name)
                warnings.append(f"Hết hạn mức {p.name}: {e}")
            except providers.ProviderError as e:
                if "timed out" in str(e) or "kết nối" in str(e):
                    self.disabled.add(p.name)
                warnings.append(f"{p.name}: {e}")
        raise RuntimeError("Không tạo được ảnh nền: " + "; ".join(warnings))
