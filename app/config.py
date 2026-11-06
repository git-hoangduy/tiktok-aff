"""Central configuration. All secrets come from .env — nothing is hardcoded."""
from __future__ import annotations

import glob
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int(name: str, default: int = 0) -> int:
    try:
        return int(os.getenv(name, str(default)).strip() or default)
    except ValueError:
        return default


# ---------- Storage (keep everything on the project drive, not C:) ----------
DATA = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve()
UPLOADS = DATA / "uploads"
OUTPUTS = DATA / "outputs"
CACHE = DATA / "cache"
MODELS = DATA / "models"
for _p in (DATA, UPLOADS, OUTPUTS, CACHE, MODELS):
    _p.mkdir(parents=True, exist_ok=True)

# rembg downloads its model here instead of C:\Users\<you>\.u2net
os.environ.setdefault("U2NET_HOME", str(MODELS))

DB_PATH = DATA / "app.db"
MUSIC_DIR = ROOT / "assets" / "music"
MUSIC_DIR.mkdir(parents=True, exist_ok=True)

# ---------- AI providers ----------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()
# Tried in order when the main model is overloaded or unavailable.
GEMINI_FALLBACK_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_FALLBACK_MODELS", "gemini-flash-lite-latest,gemini-3-flash-preview,gemini-3.1-flash-lite").split(",") if m.strip()]
# Leave empty to disable Gemini image generation (often not in the free tier).
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "").strip()

# Order of background image providers. "procedural" is local and always works.
BG_PROVIDERS = [s.strip() for s in os.getenv("BG_PROVIDERS", "pollinations,procedural").split(",") if s.strip()]
if "procedural" not in BG_PROVIDERS:
    BG_PROVIDERS.append("procedural")

POLLINATIONS_URL = os.getenv("POLLINATIONS_URL", "https://image.pollinations.ai/prompt/{prompt}").strip()
POLLINATIONS_MODEL = os.getenv("POLLINATIONS_MODEL", "flux").strip()
POLLINATIONS_TOKEN = os.getenv("POLLINATIONS_TOKEN", "").strip()

# Optional self-imposed daily call limits (0 = no limit configured).
# We never assume what a free tier allows — you set these yourself.
DAILY_LIMITS = {
    "gemini": _int("GEMINI_DAILY_LIMIT"),
    "gemini_image": _int("GEMINI_IMAGE_DAILY_LIMIT"),
    "pollinations": _int("POLLINATIONS_DAILY_LIMIT"),
}

# AI re-creation of the product photo in new scenes/angles (free Hugging Face Spaces).
HF_EDIT_SPACES = [s.strip() for s in os.getenv(
    "HF_EDIT_SPACES", "black-forest-labs/FLUX.1-Kontext-Dev,multimodalart/Qwen-Image-Edit-Fast").split(",") if s.strip()]
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()  # optional free account token = bigger daily GPU quota
# How many different AI product photos per video (each ~20-40s of free GPU). Other scenes reuse them.
AI_IMAGES_PER_VIDEO = max(1, min(6, _int("AI_IMAGES_PER_VIDEO", 3)))
HF_KONTEXT_STEPS = max(12, min(40, _int("HF_KONTEXT_STEPS", 28)))
AI_REF_SIZE = max(768, min(1600, _int("AI_REF_SIZE", 1280)))  # reference photo sent to the image AI
# Gemini checks each AI photo against the real product (free tokens); below AI_VERIFY_MIN/10 it is redone once.
GEMINI_VERIFY_MODEL = os.getenv("GEMINI_VERIFY_MODEL", "gemini-flash-lite-latest").strip()
AI_VERIFY = os.getenv("AI_VERIFY", "1").strip() not in ("0", "false", "")
AI_VERIFY_MIN = max(1, min(10, _int("AI_VERIFY_MIN", 7)))
AI_RETRIES = max(0, min(2, _int("AI_RETRIES", 1)))
# Optional local upscaler (realesrgan-ncnn-vulkan.exe). Empty = normal resize.
REALESRGAN_PATH = os.getenv("REALESRGAN_PATH", "").strip()

# ---------- Paid Pollinations (only when chosen in the render form + sk_ key) ----------
POLLINATIONS_EDIT_MODEL = os.getenv("POLLINATIONS_EDIT_MODEL", "nanobanana2").strip()
# Use the sk_ key for composite-mode backgrounds too (flux, ~0.002 pollen/image). Off by default.
POLLINATIONS_BG_USE_KEY = os.getenv("POLLINATIONS_BG_USE_KEY", "0").strip() in ("1", "true", "yes")
# image-to-video models: alias -> (pollen per second, min s, max s)
VIDEO_MODELS = {"wan-fast": (0.01, 5, 5), "seedance-pro": (0.021, 2, 10), "kling-v3.0-std": (0.084, 5, 10)}
AI_MOTION_SCENES = max(1, min(4, _int("AI_MOTION_SCENES", 2)))  # how many scenes get a real AI clip
DAILY_LIMITS["hf_edit"] = _int("HF_EDIT_DAILY_LIMIT")
DAILY_LIMITS["pollinations_pro"] = _int("POLLINATIONS_PRO_DAILY_LIMIT")

# Voice engines in order. gemini_tts = Gemini voices via GEMINI_API_KEY (free tier, small daily quota);
# gtts = Google Translate voice (no key, female only); edge_tts adds a male voice.
TTS_PROVIDERS = [s.strip() for s in os.getenv("TTS_PROVIDERS", "gemini_tts,gtts,edge_tts").split(",") if s.strip()]
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts").strip()
# Gemini prebuilt voice per gender. Empty = skip Gemini for that gender (male -> Edge NamMinh).
GEMINI_TTS_VOICES = {"female": os.getenv("GEMINI_TTS_FEMALE", "Leda").strip(),
                     "male": os.getenv("GEMINI_TTS_MALE", "").strip()}
DAILY_LIMITS["gemini_tts"] = _int("GEMINI_TTS_DAILY_LIMIT")

REMBG_MODEL = os.getenv("REMBG_MODEL", "isnet-general-use").strip()
# Used only for photos where a hand holds the product (box + negative points). Empty = show as card.
REMBG_HAND_MODEL = os.getenv("REMBG_HAND_MODEL", "sam").strip()

VOICES = {
    "female": os.getenv("VOICE_FEMALE", "vi-VN-HoaiMyNeural"),
    "male": os.getenv("VOICE_MALE", "vi-VN-NamMinhNeural"),
}

# ---------- Video ----------
VIDEO_W, VIDEO_H, FPS = 1080, 1920, 30
MOTION_SCALE = 1.5  # scenes are composed larger than output so zoom/pan stays sharp
RENDER_PRESET = os.getenv("RENDER_PRESET", "veryfast")


def _find_binary(name: str) -> str | None:
    env = os.getenv(f"{name.upper()}_PATH", "").strip()
    if env and Path(env).exists():
        return env
    found = shutil.which(name)
    if found:
        return found
    # winget installs into a per-user folder that may not be on PATH yet
    local = os.getenv("LOCALAPPDATA", "")
    patterns = [
        os.path.join(local, "Microsoft", "WinGet", "Links", f"{name}.exe"),
        os.path.join(local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", f"{name}.exe"),
        os.path.join("C:\\", "ffmpeg", "bin", f"{name}.exe"),
    ]
    for pat in patterns:
        hits = glob.glob(pat)
        if hits:
            return hits[0]
    return None


FFMPEG = _find_binary("ffmpeg")
FFPROBE = _find_binary("ffprobe")


def _find_font() -> str | None:
    env = os.getenv("FONT_PATH", "").strip()
    candidates = [env] if env else []
    win = os.path.join(os.getenv("WINDIR", "C:\\Windows"), "Fonts")
    candidates += [os.path.join(win, f) for f in ("arialbd.ttf", "segoeuib.ttf", "tahomabd.ttf", "arial.ttf")]
    candidates += [str(ROOT / "assets" / "fonts" / "font.ttf")]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


FONT_PATH = _find_font()
