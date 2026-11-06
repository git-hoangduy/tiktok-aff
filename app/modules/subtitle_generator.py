"""SubtitleGenerator: script text + per-scene voice timing -> SRT (export) and ASS (burned in)."""
from __future__ import annotations

import re
from pathlib import Path

from PIL import ImageFont

from ..config import FONT_PATH, VIDEO_H, VIDEO_W


def _chunks(text: str, max_words: int = 7) -> list[str]:
    out = []
    for phrase in re.split(r"(?<=[,.!?;:])\s+", text.strip()):
        words = phrase.split()
        if not words:
            continue
        n = max(1, -(-len(words) // max_words))  # ceil
        size = -(-len(words) // n)
        out += [" ".join(words[i:i + size]) for i in range(0, len(words), size)]
    return out


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def font_family() -> str:
    if FONT_PATH:
        try:
            return ImageFont.truetype(FONT_PATH, 20).getname()[0]
        except OSError:
            pass
    return "Arial"


class SubtitleGenerator:
    def cues(self, texts: list[str], starts: list[float], speech: list[float]) -> list[tuple[float, float, str]]:
        cues = []
        for text, start, dur in zip(texts, starts, speech):
            parts = _chunks(text)
            total_chars = sum(len(p) for p in parts) or 1
            t = start
            for p in parts:
                d = dur * len(p) / total_chars
                cues.append((t, t + d, p))
                t += d
        return cues

    def write_srt(self, cues, out_path: Path) -> Path:
        lines = []
        for i, (a, b, text) in enumerate(cues, 1):
            lines += [str(i), f"{_ts_srt(a)} --> {_ts_srt(b)}", text, ""]
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path

    def write_ass(self, cues, out_path: Path, size: int = 72) -> Path:
        fam = font_family()
        margin_v = int(VIDEO_H * 0.24)  # stay above TikTok's caption / button area
        head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_W}
PlayResY: {VIDEO_H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{fam},{size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,90,90,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events = [f"Dialogue: 0,{_ts_ass(a)},{_ts_ass(b)},Default,,0,0,0,,"
                  f"{{\\fad(60,0)\\t(0,140,\\fscx104\\fscy104)\\t(140,260,\\fscx100\\fscy100)}}"
                  f"{text.replace('{', '(').replace('}', ')')}"
                  for a, b, text in cues]
        out_path.write_text(head + "\n".join(events) + "\n", encoding="utf-8-sig")
        return out_path
