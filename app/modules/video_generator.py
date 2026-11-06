"""VideoGenerator: still scene image -> moving clip (zoom/pan/push-in, light sweep, text fade).
Pure FFmpeg, so it costs nothing and needs no GPU. An Image-to-Video AI provider can be
plugged in later (Phase 2) by implementing `animate()` differently."""
from __future__ import annotations

import math
from pathlib import Path

from ..config import FPS, RENDER_PRESET, VIDEO_H, VIDEO_W
from ..utils import ffmpeg

_CENTER_X = "iw/2-(iw/zoom/2)"
_CENTER_Y = "ih/2-(ih/zoom/2)"

MOTION_EXPR = {
    "zoom_in": ("1+0.14*{p}", _CENTER_X, _CENTER_Y),
    "zoom_out": ("1.14-0.14*{p}", _CENTER_X, _CENTER_Y),
    "push_in": ("1+0.22*(1-cos(PI*{p}))/2", _CENTER_X, _CENTER_Y),
    "pan_left": ("1.12", "(iw-iw/zoom)*(1-{p})", _CENTER_Y),
    "pan_right": ("1.12", "(iw-iw/zoom)*{p}", _CENTER_Y),
    "pan_up": ("1.12", _CENTER_X, "(ih-ih/zoom)*(1-{p})"),
}


class VideoGenerator:
    def animate(self, image: Path, overlay: Path | None, glare: Path | None, motion: str,
                seconds: float, out_path: Path) -> Path:
        frames = max(2, math.ceil(seconds * FPS))
        p = f"(on/{frames - 1})"
        z, x, y = (e.format(p=p) for e in MOTION_EXPR.get(motion, MOTION_EXPR["zoom_in"]))
        args: list[str] = ["-i", str(image)]
        filters = [f"[0:v]zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={VIDEO_W}x{VIDEO_H}:fps={FPS},"
                   f"setsar=1,format=rgba[base]"]
        last, idx = "base", 1
        if glare:
            args += ["-loop", "1", "-framerate", str(FPS), "-i", str(glare)]
            st, dur = 0.25, min(1.3, max(0.6, seconds - 0.4))
            filters.append(f"[{last}][{idx}:v]overlay=x='-w+(W+w)*(t-{st})/{dur}':y=0:"
                           f"enable='between(t,{st},{st + dur})'[g]")
            last, idx = "g", idx + 1
        if overlay:
            args += ["-loop", "1", "-framerate", str(FPS), "-i", str(overlay)]
            filters.append(f"[{idx}:v]format=rgba,fade=t=in:st=0.15:d=0.35:alpha=1[ov]")
            filters.append(f"[{last}][ov]overlay=0:0[o]")
            last, idx = "o", idx + 1
        filters.append(f"[{last}]format=yuv420p[out]")
        args += ["-filter_complex", ";".join(filters), "-map", "[out]", "-frames:v", str(frames),
                 "-r", str(FPS), "-c:v", "libx264", "-preset", RENDER_PRESET, "-crf", "18",
                 "-pix_fmt", "yuv420p", "-an", str(out_path)]
        ffmpeg.run(args)
        return out_path

    def animate_video(self, src: Path, overlay: Path | None, seconds: float, out_path: Path) -> Path:
        """AI motion clip -> scene clip: fill 9:16, hold the last frame if the clip is short, add the overlay."""
        frames = max(2, math.ceil(seconds * FPS))
        args: list[str] = ["-i", str(src)]
        filters = [f"[0:v]scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase:flags=lanczos,"
                   f"crop={VIDEO_W}:{VIDEO_H},fps={FPS},setsar=1,tpad=stop_mode=clone:stop_duration={seconds:.3f},"
                   f"format=rgba[base]"]
        last = "base"
        if overlay:
            args += ["-loop", "1", "-framerate", str(FPS), "-i", str(overlay)]
            filters.append("[1:v]format=rgba,fade=t=in:st=0.15:d=0.35:alpha=1[ov]")
            filters.append(f"[{last}][ov]overlay=0:0[o]")
            last = "o"
        filters.append(f"[{last}]format=yuv420p[out]")
        args += ["-filter_complex", ";".join(filters), "-map", "[out]", "-frames:v", str(frames),
                 "-r", str(FPS), "-c:v", "libx264", "-preset", RENDER_PRESET, "-crf", "18",
                 "-pix_fmt", "yuv420p", "-an", str(out_path)]
        ffmpeg.run(args)
        return out_path
