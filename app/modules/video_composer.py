"""VideoComposer: scene clips + transitions + burned subtitles + voice + ducked music -> TikTok MP4."""
from __future__ import annotations

import shutil
from pathlib import Path

from ..config import FONT_PATH, FPS
from ..utils import ffmpeg

TRANSITION = 0.35  # seconds of overlap between scenes


class VideoComposer:
    def compose(self, workdir: Path, clips: list[Path], slots: list[float], transitions: list[str],
                narration: Path | None, subs_ass: Path | None, out_path: Path, music: Path | None = None,
                voice_volume: float = 1.0, music_volume: float = 0.15) -> Path:
        n = len(clips)
        total = sum(slots)
        args: list[str] = []
        for c in clips:
            args += ["-i", c.name]
        if narration:
            args += ["-i", narration.name]
        else:  # no voice: silent track (or music only) so the MP4 still has normal AAC audio
            args += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
        if music:
            args += ["-stream_loop", "-1", "-i", str(music)]

        f: list[str] = []
        last = "0:v"
        offset = 0.0
        for k in range(1, n):
            offset += slots[k - 1]
            tr = transitions[k] if k < len(transitions) else "fade"
            f.append(f"[{last}][{k}:v]xfade=transition={tr}:duration={TRANSITION}:offset={offset:.3f}[x{k}]")
            last = f"x{k}"
        vchain = f"[{last}]"
        if subs_ass:
            fonts = workdir / "fonts"
            fonts.mkdir(exist_ok=True)
            if FONT_PATH:
                shutil.copy(FONT_PATH, fonts / Path(FONT_PATH).name)
            f.append(f"{vchain}subtitles={subs_ass.name}:fontsdir=fonts,format=yuv420p[v]")
        else:
            f.append(f"{vchain}format=yuv420p[v]")

        a = n
        if music and not narration:
            m = n + 1
            fade_st = max(0.0, total - 1.5)
            f.append(f"[{m}:a]aformat=sample_rates=44100:channel_layouts=stereo,volume={music_volume:.2f},"
                     f"atrim=0:{total:.3f},afade=t=out:st={fade_st:.3f}:d=1.5[a]")
        elif music:
            m = n + 1
            fade_st = max(0.0, total - 1.5)
            f.append(f"[{a}:a]volume={voice_volume:.2f},asplit=2[nar][key]")
            f.append(f"[{m}:a]aformat=sample_rates=44100:channel_layouts=stereo,volume={music_volume:.2f},"
                     f"atrim=0:{total:.3f},afade=t=out:st={fade_st:.3f}:d=1.5[mus0]")
            f.append("[mus0][key]sidechaincompress=threshold=0.04:ratio=5:attack=20:release=350[mus]")
            f.append("[nar][mus]amix=inputs=2:duration=first:normalize=0[a]")
        else:
            f.append(f"[{a}:a]volume={voice_volume:.2f}[a]")

        tmp = workdir / "final_tmp.mp4"
        args += ["-filter_complex", ";".join(f), "-map", "[v]", "-map", "[a]",
                 "-t", f"{total:.3f}", "-r", str(FPS),
                 "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-profile:v", "high",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
                 "-movflags", "+faststart", tmp.name]
        ffmpeg.run(args, cwd=workdir, timeout=1800)
        shutil.move(str(tmp), str(out_path))
        return out_path

    @staticmethod
    def thumbnail(video: Path, out_jpg: Path, at: float = 1.0) -> Path:
        ffmpeg.run(["-ss", f"{at:.2f}", "-i", video, "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", out_jpg])
        return out_jpg
