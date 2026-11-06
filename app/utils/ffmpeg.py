"""Thin wrappers around the ffmpeg / ffprobe executables."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..config import FFMPEG, FFPROBE

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class FFmpegError(RuntimeError):
    pass


def require() -> None:
    if not FFMPEG or not FFPROBE:
        raise FFmpegError("Không tìm thấy FFmpeg. Cài bằng: winget install Gyan.FFmpeg (rồi mở lại terminal) "
                          "hoặc đặt FFMPEG_PATH / FFPROBE_PATH trong .env")


def run(args: list[str], cwd: Path | None = None, timeout: int = 900) -> None:
    require()
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *map(str, args)]
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, creationflags=_NO_WINDOW)
    if p.returncode != 0:
        raise FFmpegError(f"FFmpeg lỗi: {p.stderr.strip()[-1200:]}")


def duration(path: Path) -> float:
    require()
    p = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW)
    try:
        return float(json.loads(p.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise FFmpegError(f"Không đọc được thời lượng {path.name}: {p.stderr[-300:]}") from e


def version() -> str | None:
    if not FFMPEG:
        return None
    try:
        p = subprocess.run([FFMPEG, "-version"], capture_output=True, text=True, timeout=20,
                           creationflags=_NO_WINDOW)
        return p.stdout.splitlines()[0] if p.stdout else None
    except Exception:
        return None
