"""ScriptGenerator: analysis -> N editable TikTok scripts (each split into scenes)."""
from __future__ import annotations

import re

from .. import providers
from ..prompts import VISUALS

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️#*]+")


# Hype words some models still slip in despite the prompt; removed rather than trusted.
_HYPE = re.compile(r"\s*\b(quốc dân|thần thánh|số một|số 1|tốt nhất thị trường|100%)\b", re.I)


def clean_text(t: str) -> str:
    t = _EMOJI.sub("", str(t or ""))
    t = _HYPE.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def normalize_script(s: dict) -> dict | None:
    scenes = []
    for sc in s.get("scenes") or []:
        text = clean_text(sc.get("text", ""))
        if not text:
            continue
        visual = sc.get("visual") if sc.get("visual") in VISUALS else "context"
        scenes.append({"text": text, "visual": visual,
                       "background_prompt": clean_text(sc.get("background_prompt", "")) or
                       "minimal studio podium with soft gradient backdrop"})
    if not scenes:
        # Build scenes from the summary fields if the model skipped them.
        parts = [s.get("hook"), s.get("intro"), *(s.get("benefits") or []), s.get("cta")]
        scenes = [{"text": clean_text(p), "visual": "context",
                   "background_prompt": "minimal studio podium with soft gradient backdrop"}
                  for p in parts if clean_text(p or "")]
    if not scenes:
        return None
    scenes = scenes[:7]
    scenes[0]["visual"] = "intro"
    if len(scenes) > 1:
        scenes[-1]["visual"] = "cta"
    return {
        "title": clean_text(s.get("title", "")) or "Kịch bản",
        "hook": clean_text(s.get("hook", "")) or scenes[0]["text"],
        "intro": clean_text(s.get("intro", "")),
        "benefits": [clean_text(b) for b in (s.get("benefits") or []) if clean_text(b)],
        "cta": clean_text(s.get("cta", "")) or scenes[-1]["text"],
        "trend_used": clean_text(s.get("trend_used", "")),
        "comparison": clean_text(s.get("comparison", "")),
        "caption": clean_text(s.get("caption", "")),
        "hashtags": _tags(s.get("hashtags")),
        "scenes": scenes,
    }


def _tags(v) -> list[str]:
    if isinstance(v, str):
        v = v.replace(",", " ").split()
    out = []
    for t in v or []:
        t = re.sub(r"[^\wÀ-ỹ]", "", str(t))
        if t and f"#{t}" not in out:
            out.append(f"#{t}")
    return out[:12]


class ScriptGenerator:
    def generate(self, analysis: dict, info: dict, style: str, duration: int,
                 count: int = 3, trends: dict | None = None) -> tuple[list[dict], str, list[str]]:
        warnings: list[str] = []
        for p in providers.llm_chain():
            try:
                raw = p.generate_scripts(analysis, info, style, duration, count, trends)
                scripts = [n for n in (normalize_script(s) for s in raw) if n]
                if scripts:
                    return scripts[:count], p.name, warnings
                warnings.append(f"{p.name}: kịch bản rỗng")
            except providers.ProviderError as e:
                warnings.append(f"{p.name}: {e}")
        raise RuntimeError("Không tạo được kịch bản: " + "; ".join(warnings))
