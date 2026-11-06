"""VoiceGenerator: one audio file per scene so scene length == spoken length (perfect sync)."""
from __future__ import annotations

import wave
from pathlib import Path

from .. import providers
from ..config import VOICES
from ..utils import ffmpeg

RATE, CHANNELS = 44100, 2


class VoiceGenerator:
    def __init__(self) -> None:
        self.tts = providers.tts()
        self.warnings: list[str] = []

    def speak(self, text: str, voice_key: str, rate: int, out_mp3: Path) -> float:
        voice = VOICES.get(voice_key, voice_key)
        self.tts.synthesize(text, voice, rate, out_mp3)
        return ffmpeg.duration(out_mp3)

    def generate(self, scenes: list[dict], voice_key: str, target: float, workdir: Path,
                 rate: int = 5) -> tuple[list[float], int]:
        """Synthesize every scene, speeding up a little if the script is too long for `target`.
        Writes scene_XX.mp3 and returns (durations, rate_used)."""
        durations: list[float] = []
        for _ in range(3):
            self.tts.used.clear()
            durations = [self.speak(sc["text"], voice_key, rate, workdir / f"voice_{i:02d}.mp3")
                         for i, sc in enumerate(scenes)]
            if len(set(self.tts.used)) > 1:
                # Mixed voices sound wrong: redo everything with the engine that kept working.
                order = [p.name for p in self.tts.providers]
                keep = max(set(self.tts.used), key=order.index)
                self.tts.providers = [p for p in self.tts.providers if p.name == keep]
                self.warnings.append(f"Giọng chính bị lỗi → dùng {keep} cho cả video")
                continue
            total = sum(durations) + 0.3 * len(scenes)
            if total <= target * 1.12 or rate >= 30:
                break
            rate = min(30, rate + int((total / target - 1) * 100) + 3)
        return durations, rate

    @staticmethod
    def slots(durations: list[float], target: float) -> list[float]:
        """Time each scene stays on screen: speech + breathing room (+ longer hold on the CTA)."""
        pads = [0.3] * len(durations)
        pads[-1] = 0.9
        total = sum(durations) + sum(pads)
        if total < target * 0.85:  # short script: breathe a bit more, up to +0.8s/scene
            extra = min(0.8, (target * 0.85 - total) / len(durations))
            pads = [p + extra for p in pads]
        return [round(d + p, 3) for d, p in zip(durations, pads)]

    @staticmethod
    def silent_slots(n: int, target: float) -> list[float]:
        """No narration: split the chosen duration evenly, the CTA scene holds a little longer."""
        base = target / (n + 0.3)
        return [round(base, 3)] * (n - 1) + [round(target - base * (n - 1), 3)]

    @staticmethod
    def build_narration(workdir: Path, slots: list[float], out_wav: Path) -> Path:
        parts = []
        for i, slot in enumerate(slots):
            part = workdir / f"voice_{i:02d}.wav"
            ffmpeg.run(["-i", workdir / f"voice_{i:02d}.mp3", "-af", "apad", "-t", f"{slot:.3f}",
                        "-ar", str(RATE), "-ac", str(CHANNELS), "-c:a", "pcm_s16le", part])
            parts.append(part)
        with wave.open(str(out_wav), "wb") as out:
            out.setnchannels(CHANNELS)
            out.setsampwidth(2)
            out.setframerate(RATE)
            for part, slot in zip(parts, slots):
                with wave.open(str(part), "rb") as w:
                    frames = w.readframes(w.getnframes())
                need = int(round(slot * RATE)) * CHANNELS * 2  # exact length keeps scenes in sync
                out.writeframes(frames[:need].ljust(need, b"\x00"))
        return out_wav
