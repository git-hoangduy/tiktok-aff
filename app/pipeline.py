"""Orchestrates the modules and runs jobs one at a time in a background thread
(one job at a time keeps RAM low on modest PCs)."""
from __future__ import annotations

import queue
import random
import re
import shutil
import threading
import traceback
from pathlib import Path
from typing import Callable

from . import db, providers
from .config import AI_MOTION_SCENES, DATA, MOTION_SCALE, MUSIC_DIR, OUTPUTS, UPLOADS, VIDEO_H, VIDEO_MODELS, VIDEO_W
from .modules.background_remover import BackgroundRemover
from .providers.base import ProviderError, QuotaExceeded
from .modules.ai_shots import AIShots, motion_prompt, motion_scenes, rank_refs
from .providers import pollinations_ai as pro_api
from .providers.hf_edit import cover_frame
from .modules.image_generator import ImageGenerator
from .modules.product_analyzer import ProductAnalyzer
from .modules.product_compositor import ProductCompositor, make_glare
from .modules.scene_planner import ScenePlanner
from .modules.script_generator import ScriptGenerator
from .modules.subtitle_generator import SubtitleGenerator
from .modules.trend_researcher import TrendResearcher
from .modules.video_composer import TRANSITION, VideoComposer
from .modules.video_generator import VideoGenerator
from .modules.voice_generator import VoiceGenerator
from .utils.imaging import dominant_colors, load_any

DEFAULT_CONFIG = {"duration": 30, "voice": "female", "style": "review", "versions": 1,
                  "music": "", "voice_volume": 1.0, "music_volume": 0.15, "local_only": False,
                  "image_mode": "ai", "ai_motion": ""}
# image_mode: ai = re-create product photos with free AI; ai_pro = paid Pollinations; composite = real cutout.
# ai_motion: paid image-to-video model for a few scenes ("" = free FFmpeg motion).

# ------------------------------------------------------------------ job queue
_jobs: "queue.Queue[tuple[str, Callable, tuple]]" = queue.Queue()
_worker: threading.Thread | None = None


def _loop() -> None:
    while True:
        name, fn, args = _jobs.get()
        try:
            fn(*args)
        except Exception:
            traceback.print_exc()
        finally:
            _jobs.task_done()


def submit(name: str, fn: Callable, *args) -> None:
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_loop, daemon=True, name="pipeline-worker")
        _worker.start()
    _jobs.put((name, fn, args))


def pending_jobs() -> int:
    return _jobs.qsize()


# ------------------------------------------------------------------ helpers
def rel(p: Path | str | None) -> str | None:
    if not p:
        return None
    return Path(p).resolve().relative_to(DATA).as_posix()


def absp(r: str | None) -> Path | None:
    return (DATA / r) if r else None


def product_info(p: dict) -> dict:
    return {"name": p.get("name", ""), "price": p.get("price", ""), "link": p.get("link", ""),
            "user_info": p.get("user_info", ""), "category": p.get("category", "auto")}


def product_images(pid: int) -> list[dict]:
    return db.query("SELECT * FROM product_images WHERE product_id=? ORDER BY position, id", (pid,))


def music_files() -> list[str]:
    return sorted(f.name for f in MUSIC_DIR.iterdir() if f.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg"))


def _set_product(pid: int, status: str, message: str = "") -> None:
    db.update("products", pid, status=status, message=message, updated_at=db.now())


def _set_video(vid: int, **kw) -> None:
    db.update("videos", vid, updated_at=db.now(), **kw)


# ------------------------------------------------------------------ analysis + scripts
def analyze_product(pid: int, force: bool = False) -> dict:
    p = db.one("SELECT * FROM products WHERE id=?", (pid,))
    if p["analysis_json"] and not force:
        return db.loads(p["analysis_json"])  # saved analysis is reused across videos
    rows = product_images(pid)
    analysis, source, warnings = ProductAnalyzer().analyze([absp(i["path"]) for i in rows], product_info(p))
    db.update("products", pid, analysis_json=analysis, analysis_source=source, updated_at=db.now())
    for r in rows:  # new product boxes -> cutouts must be redone
        db.update("product_images", r["id"], cutout_path=None, crop_path=None, mode=None, reason=None)
    if warnings:
        _set_product(pid, "running", "Đã chuyển sang phương án dự phòng: " + "; ".join(warnings))
    return analysis


def ensure_staging(pid: int) -> tuple[str, bool, str]:
    """How the product is realistically shown (e.g. 'fitted on an iPhone, held in one hand').
    Comes with the analysis; older analyses get it from one tiny text call, then it is cached."""
    from .prompts import STAGING_BY_CATEGORY
    p = db.one("SELECT * FROM products WHERE id=?", (pid,))
    analysis = db.loads(p["analysis_json"], {}) or {}
    if analysis.get("staging") and "props" in analysis:
        return analysis["staging"], bool(analysis.get("handheld")), analysis["props"]
    staging, handheld, props = None, False, ""
    imgs = db.query("SELECT * FROM product_images WHERE product_id=? ORDER BY position, id", (pid,))
    best = analysis.get("best_main_image", 0)
    main = absp(imgs[best]["path"]) if 0 <= best < len(imgs) else (absp(imgs[0]["path"]) if imgs else None)
    for prov in providers.llm_chain():
        if not hasattr(prov, "staging"):
            continue
        try:
            d = prov.staging(analysis, product_info(p), main)
            staging, handheld = str(d.get("staging", "")).strip()[:200], d.get("handheld") is True
            props = str(d.get("props", "")).strip().rstrip(".")[:300]
            break
        except providers.ProviderError:
            continue
    if not staging:
        cat = p.get("category") if p.get("category") in STAGING_BY_CATEGORY else analysis.get("category_key", "other")
        staging, handheld = STAGING_BY_CATEGORY.get(cat, STAGING_BY_CATEGORY["other"])
    analysis.update(staging=staging, handheld=handheld, props=props or analysis.get("props", ""))
    db.update("products", pid, analysis_json=analysis)
    return staging, handheld, analysis["props"]


TREND_MAX_AGE_HOURS = 24


def latest_trends(pid: int) -> dict | None:
    row = db.one("SELECT * FROM trends WHERE product_id=? ORDER BY id DESC LIMIT 1", (pid,))
    if not row:
        return None
    data = db.loads(row["data_json"], {})
    data["_id"], data["_created_at"] = row["id"], row["created_at"]
    return data


def research_trends(pid: int, force: bool = False) -> dict | None:
    """Fresh trend report (cached for 24h per product). Never blocks video creation on failure."""
    from datetime import datetime, timedelta
    cached = latest_trends(pid)
    if cached and not force:
        try:
            if datetime.now() - datetime.fromisoformat(cached["_created_at"]) < timedelta(hours=TREND_MAX_AGE_HOURS):
                return cached
        except (KeyError, ValueError):
            pass
    p = db.one("SELECT * FROM products WHERE id=?", (pid,))
    analysis = db.loads(p["analysis_json"]) or analyze_product(pid)
    links = [x for x in re.split(r"[\s,]+", p.get("ref_links") or "") if x.startswith("http")]
    category = p.get("category") if p.get("category") not in (None, "", "auto") else analysis.get("category_key")
    if not links and not force:
        # Same category researched in the last 24h (another product)? Reuse it — saves a search + an LLM call.
        since = (datetime.now() - timedelta(hours=TREND_MAX_AGE_HOURS)).isoformat(timespec="seconds")
        for row in db.query("SELECT * FROM trends WHERE created_at >= ? ORDER BY id DESC LIMIT 20", (since,)):
            data = db.loads(row["data_json"], {})
            if data.get("category") == category and data.get("sources") and not any(
                    s.get("type") == "reference" for s in data["sources"]):
                data["reused_from_product"] = row["product_id"]
                db.insert("trends", product_id=pid, data_json=data, created_at=row["created_at"])
                return latest_trends(pid)
    try:
        data = TrendResearcher().research(analysis, product_info(p), links)
    except Exception as e:
        traceback.print_exc()
        _set_product(pid, "running", f"Không nghiên cứu được xu hướng ({e}) — viết kịch bản không có trend")
        return cached
    if not data["sources"]:
        _set_product(pid, "running", "Không tìm được dữ liệu xu hướng (mạng?) — viết kịch bản không có trend")
    db.insert("trends", product_id=pid, data_json=data, created_at=db.now())
    return latest_trends(pid)


def generate_scripts(pid: int, style: str, duration: int, count: int = 3) -> list[int]:
    p = db.one("SELECT * FROM products WHERE id=?", (pid,))
    analysis = db.loads(p["analysis_json"]) or analyze_product(pid)
    trends = latest_trends(pid)
    scripts, source, warnings = ScriptGenerator().generate(analysis, product_info(p), style, duration, count, trends)
    ids = [db.insert("scripts", product_id=pid, style=style, duration=duration, data_json=s, source=source,
                     created_at=db.now(), updated_at=db.now()) for s in scripts]
    if warnings:
        _set_product(pid, "running", "Kịch bản dùng phương án dự phòng: " + "; ".join(warnings))
    return ids


def job_prepare(pid: int, style: str, duration: int, force_analysis: bool = False,
                force_trends: bool = False) -> None:
    """Analyze (if needed), research current trends, then write 3 scripts for the user to review."""
    try:
        _set_product(pid, "running", "Đang phân tích sản phẩm...")
        analyze_product(pid, force_analysis)
        _set_product(pid, "running", "Đang tìm & tách sản phẩm chính khỏi từng ảnh...")
        prepare_images(pid)
        _set_product(pid, "running", "Đang tìm video hot & xu hướng TikTok hiện tại...")
        research_trends(pid, force_trends)
        _set_product(pid, "running", "Đang viết 3 kịch bản theo xu hướng...")
        generate_scripts(pid, style, duration, 3)
        prev = db.one("SELECT message FROM products WHERE id=?", (pid,))["message"]
        keep = "dự phòng" in prev or "xu hướng" in prev
        _set_product(pid, "ready", prev if keep else "Đã xong. Hãy xem xu hướng & chỉnh sửa kịch bản.")
    except Exception as e:
        traceback.print_exc()
        _set_product(pid, "error", str(e))


def job_auto(pid: int, cfg: dict) -> None:
    """One-click flow: analyze -> 3 scripts -> render `versions` videos."""
    job_prepare(pid, cfg["style"], cfg["duration"])
    if db.one("SELECT status FROM products WHERE id=?", (pid,))["status"] == "error":
        return
    scripts = db.query("SELECT id FROM scripts WHERE product_id=? ORDER BY id DESC LIMIT 3", (pid,))
    scripts.reverse()
    chosen = scripts[: max(1, int(cfg.get("versions", 1)))]
    if not chosen:
        _set_product(pid, "error", "Không có kịch bản nào để dựng video")
        return
    vids = [create_video(pid, s["id"], cfg, enqueue=False) for s in chosen]
    for k, vid in enumerate(vids, 1):
        _set_product(pid, "running", f"Đang dựng video {k}/{len(vids)}...")
        render_video(vid)
    ok = db.one(f"SELECT COUNT(*) n FROM videos WHERE status='done' AND id IN ({','.join('?' * len(vids))})", vids)["n"]
    _set_product(pid, "ready" if ok else "error", f"Đã tạo xong {ok}/{len(vids)} video.")


def create_video(pid: int, script_id: int, cfg: dict, enqueue: bool = True) -> int:
    vid = db.insert("videos", product_id=pid, script_id=script_id, status="queued", progress=0,
                    message="Đang chờ...", config_json={**DEFAULT_CONFIG, **cfg},
                    created_at=db.now(), updated_at=db.now())
    if enqueue:
        submit(f"render-{vid}", render_video, vid)
    return vid


# ------------------------------------------------------------------ rendering
def prepare_images(pid: int, process: bool = True) -> list[dict]:
    """Find & isolate the main product in every photo (cached per image), then return the photos
    to use, main photo first, unusable ones (size charts, text-only banners...) left out.
    process=False only returns that ordering (used by the web pages; scene.image_index refers to it)."""
    rows = product_images(pid)
    p = db.one("SELECT analysis_json FROM products WHERE id=?", (pid,))
    analysis = db.loads(p["analysis_json"], {}) if p else {}
    metas = analysis.get("images") or []
    remover = BackgroundRemover()
    for order, im in enumerate(rows):
        im["meta"] = metas[order] if order < len(metas) else {}
        if not process or im.get("mode") and (im["mode"] != "cutout" or (im["cutout_path"] and absp(im["cutout_path"]).exists())):
            continue  # already processed
        src = absp(im["path"])
        try:
            r = remover.process(src, src.parent, im["id"], im["meta"])
        except Exception as e:
            traceback.print_exc()
            r = {"mode": "card", "cutout": None, "crop": None, "photo_ok": False, "reason": f"lỗi: {e}"}
        im.update(mode=r["mode"], cutout_path=rel(r["cutout"]), crop_path=rel(r["crop"]),
                  photo_ok=int(bool(r["photo_ok"])), reason=r["reason"][:300])
        db.update("product_images", im["id"], mode=im["mode"], cutout_path=im["cutout_path"],
                  crop_path=im["crop_path"], photo_ok=im["photo_ok"], reason=im["reason"])
    for im in rows:
        im.setdefault("meta", {})
        im["role"] = im["meta"].get("role", "other")
    usable = [im for im in rows if im["mode"] != "skip"] or rows  # never end up with no photo at all
    best = analysis.get("best_main_image", 0)
    main = next((im for k, im in enumerate(rows) if k == best and im in usable), None)
    if main is None or main["mode"] != "cutout":
        # prefer a clean cutout as the hero shot when the chosen one couldn't be isolated
        main = next((im for im in usable if im["mode"] == "cutout"), main or usable[0])
    return [main] + [im for im in usable if im is not main]


def _product_source(im: dict) -> tuple[Path, bool]:
    """(image to composite, is_transparent_cutout). Cards use the product crop, not the whole photo."""
    if im.get("mode") == "cutout" and im.get("cutout_path"):
        return absp(im["cutout_path"]), True
    if im.get("crop_path") and absp(im["crop_path"]).exists():
        return absp(im["crop_path"]), False
    return absp(im["path"]), False


def render_video(vid: int, regen: dict | None = None) -> None:
    """Build (or rebuild) a video. Existing scene assets are reused, so regenerating one
    scene only redoes that scene's background/voice plus the final mix."""
    warnings: list[str] = []
    try:
        v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
        cfg = {**DEFAULT_CONFIG, **(db.loads(v["config_json"]) or {})}
        product = db.one("SELECT * FROM products WHERE id=?", (v["product_id"],))
        srow = db.one("SELECT data_json FROM scripts WHERE id=?", (v["script_id"],)) if v["script_id"] else None
        script = db.loads(srow["data_json"], {}) if srow else {}
        wd = OUTPUTS / f"video_{vid}"
        wd.mkdir(parents=True, exist_ok=True)
        _set_video(vid, status="running", progress=3, message="Tách nền sản phẩm...")

        imgs = prepare_images(product["id"])
        try:
            colors = dominant_colors(load_any(_product_source(imgs[0])[0]), 4)
        except Exception:
            colors = []
        colors = colors or [(200, 190, 230)]
        accent = colors[0]

        scenes = db.loads(v["scenes_json"])
        if not scenes:
            if not script or not script.get("scenes"):
                raise RuntimeError("Kịch bản của video này không còn (đã bị xóa?) — hãy dựng từ kịch bản khác")
            scenes = ScenePlanner().plan(script, len(imgs), cfg["style"], seed=random.randrange(1 << 30),
                                         product_name=product.get("name") or "",
                                         images=[{"role": im["role"], "mode": im["mode"],
                                                  "photo_ok": bool(im.get("photo_ok"))} for im in imgs])
        if regen and not 0 <= regen["idx"] < len(scenes):
            raise RuntimeError("Cảnh cần tạo lại không tồn tại — hãy dựng lại toàn bộ")
        if regen:
            sc = scenes[regen["idx"]]
            if regen.get("text") and regen["text"].strip() != sc["text"]:
                sc["text"] = regen["text"].strip()
                sc.pop("speech", None)
            if regen.get("bg_prompt") is not None and regen["bg_prompt"].strip():
                sc["bg_prompt"] = regen["bg_prompt"].strip()
                sc["setting"] = sc["bg_prompt"].split(", empty clean surface")[0]
                sc["setting_custom"] = True  # user picked this location: don't force the video's shared one
            if regen.get("new_background", True):
                sc["seed"] = random.randrange(1, 10_000_000)
                sc.pop("bg_path", None)
            if regen.get("image_index") is not None:
                sc["image_index"] = int(regen["image_index"]) % len(imgs)
                sc["own_ref"] = True  # user picked this photo: build from it, not from the anchor shot
                if sc["layout"] == "photo" and not imgs[sc["image_index"]].get("photo_ok"):
                    sc["layout"] = "context"  # chosen photo isn't a lifestyle shot: composite it instead
            if regen.get("motion"):
                sc["motion"] = regen["motion"]
            if regen.get("new_background", True) or regen.get("image_index") is not None:
                # own AI photo for this scene from now on (don't change other scenes sharing the old one)
                sc["ai_slot"] = f"r{regen['idx']}_{sc['seed']}"
                sc.pop("ai_path", None)
            sc.pop("frame_path", None)
            sc.pop("clip_path", None)

        # ---- voice ("none" = silent video without subtitles; music is added later on TikTok)
        voice = VoiceGenerator()
        target = float(cfg["duration"])
        silent = cfg.get("voice") == "none"
        if not silent:
            _set_video(vid, progress=10, message="Tạo giọng đọc...")
        if silent:
            slots = VoiceGenerator.silent_slots(len(scenes), target)
        elif any("speech" not in s for s in scenes):
            if all("speech" not in s for s in scenes):
                durs, rate = voice.generate(scenes, cfg["voice"], target, wd)
                tts_used = voice.tts.used[-1] if voice.tts.used else voice.tts.providers[0].name
                for s, d in zip(scenes, durs):
                    s["speech"], s["rate"], s["tts"] = d, rate, tts_used
                warnings += voice.warnings
            else:
                # keep the same voice engine as the rest of the video
                engine = next((s.get("tts") for s in scenes if s.get("tts")), None)
                if engine and any(p.name == engine for p in voice.tts.providers):
                    # same engine first; others only if it is out of quota
                    voice.tts.providers.sort(key=lambda p: p.name != engine)
                engine = engine or voice.tts.providers[0].name
                for i, s in enumerate(scenes):
                    if "speech" not in s:
                        rate = scenes[0].get("rate", 5)
                        s["speech"] = voice.speak(s["text"], cfg["voice"], rate, wd / f"voice_{i:02d}.mp3")
                        s["rate"], s["tts"] = rate, voice.tts.used[-1] if voice.tts.used else engine
        if not silent:
            slots = voice.slots([s["speech"] for s in scenes], target)

        # ---- scene images + clips
        ig, comp, vg = ImageGenerator(), ProductCompositor(), VideoGenerator()
        glare = make_glare(wd / "glare.png", VIDEO_H)
        cw, ch = int(VIDEO_W * MOTION_SCALE), int(VIDEO_H * MOTION_SCALE)
        n = len(scenes)
        # AI mode: the product photo is re-created by an image model in each scene's setting/angle.
        # "ai" = free FLUX Kontext (HF); "ai_pro" = paid Pollinations multi-reference model, chosen by the user.
        ai_mode = cfg.get("image_mode", "ai") in ("ai", "ai_pro") and not cfg.get("local_only")
        ai_paths = {s["ai_slot"]: s["ai_path"] for s in scenes
                    if s.get("ai_path") and s.get("ai_slot") is not None and absp(s["ai_path"]).exists()}
        staging, handheld, props = ensure_staging(product["id"]) if ai_mode else ("", False, "")
        shots = AIShots(cfg.get("image_mode") == "ai_pro", staging, handheld, props, rank_refs(imgs)) if ai_mode else None
        # Consistency: scene 1's AI photo is the ANCHOR (made from the real product photos). Every other
        # AI photo is made from that anchor with only the camera changed, so the phone, hand, background
        # and light stay the same across the video. All shots share scene 1's location.
        anchor_slot = scenes[0].get("ai_slot", 0)
        video_setting = scenes[0].get("setting") or scenes[0]["bg_prompt"].split(", empty clean surface")[0]
        # Paid AI motion clips: only when the user ticked it (cost shown in the form) and a sk_ key exists.
        motion_model = cfg.get("ai_motion") if ai_mode and cfg.get("ai_motion") in VIDEO_MODELS else ""
        if motion_model and not pro_api.available():
            warnings.append("Chuyển động AI: chưa có POLLINATIONS_TOKEN (sk_...) → dùng chuyển động FFmpeg")
            motion_model = ""
        motion_idx = motion_scenes(scenes, AI_MOTION_SCENES) if motion_model else set()
        motion_off, paid = False, 0.0
        for i, sc in enumerate(scenes):
            _set_video(vid, progress=15 + int(60 * i / n), message=f"Dựng cảnh {i + 1}/{n}...")
            im = imgs[sc["image_index"] % len(imgs)]
            photo = absp(im["path"])
            if not sc.get("frame_path"):
                frame = wd / f"frame_{i:02d}.jpg"
                done = False
                sc.pop("motion_src", None)  # new picture -> old AI clip no longer matches
                if ai_mode:
                    slot = sc.get("ai_slot", i)
                    path = ai_paths.get(slot)
                    if not path and not shots.off:
                        _set_video(vid, message=f"Dựng cảnh {i + 1}/{n}: AI đang tạo ảnh sản phẩm (~30-90s)...")
                        anchor = ai_paths.get(anchor_slot)
                        anchor_p = absp(anchor) if slot != anchor_slot and anchor and absp(anchor).exists() else None
                        own_ref = None
                        if sc.get("own_ref"):
                            own_ref = absp(im["crop_path"]) if im.get("crop_path") and absp(im["crop_path"]).exists() else photo
                            anchor_p = None
                        res = shots.make(sc["visual"], wd / f"ai_{i:02d}_{sc['seed']}.jpg", sc["seed"], anchor_p,
                                         sc.get("setting") if sc.get("setting_custom") else "", video_setting, own_ref)
                        if res:
                            path = ai_paths[slot] = rel(res[0])
                            sc["ai_provider"] = res[1]
                    if path:
                        cover_frame(absp(path), frame, cw, ch)
                        src_sc = next((s for s in scenes if s.get("ai_path") == path and s.get("ai_provider")), sc)
                        sc["ai_path"] = path
                        sc["bg_provider"] = src_sc.get("ai_provider") or sc.get("ai_provider") or "AI tái tạo"
                        done = True
                anchor = ai_paths.get(anchor_slot)
                if not done and ai_mode and anchor and absp(anchor).exists() and not sc.get("own_ref"):
                    # AI ran out after the anchor: reuse it (different camera motion) so the look stays the same
                    cover_frame(absp(anchor), frame, cw, ch)
                    sc["bg_provider"] = "AI tái tạo (dùng lại ảnh cảnh 1)"
                    done = True
                if not done and ai_mode:
                    # AI unavailable (quota): show the untouched original photo, framed — no rough cutout edges
                    ref = absp(im["crop_path"]) if im.get("crop_path") and absp(im["crop_path"]).exists() else photo
                    comp.photo_frame(ref, frame, cw, ch)
                    sc["bg_provider"] = "ảnh gốc (AI chưa chạy được)"
                    done = True
                if not done:  # composite mode -> real cutout on generated background
                    full_photo = sc["layout"] == "photo"
                    if not full_photo and (not sc.get("bg_path") or not absp(sc["bg_path"]).exists()):
                        bg = wd / f"bg_{i:02d}_{sc['seed']}.jpg"
                        ctx = {"colors": colors, "photo": photo, "category": product.get("category")}
                        _, provider, w = ig.background(sc["bg_prompt"], sc["seed"], bg, ctx, cw, ch,
                                                       cfg.get("local_only"))
                        sc["bg_path"], sc["bg_provider"] = rel(bg), provider
                        warnings += w
                    if full_photo:
                        comp.photo_frame(photo, frame, cw, ch)
                        sc["bg_provider"] = "ảnh thật"
                    else:
                        src, is_cut = _product_source(im)
                        comp.compose(absp(sc["bg_path"]), src, is_cut, sc["layout"], frame, cw, ch)
                ov = comp.overlay(sc.get("overlay"), accent, product.get("price", ""),
                                  wd / f"overlay_{i:02d}.png", VIDEO_W, VIDEO_H)
                sc["frame_path"], sc["overlay_path"] = rel(frame), rel(ov)
                sc.pop("clip_path", None)
            clip_len = round(slots[i] + (TRANSITION if i < n - 1 else 0), 3)
            msrc = absp(sc.get("motion_src"))
            if (i in motion_idx and not motion_off and sc.get("ai_path") and not (msrc and msrc.exists())):
                _set_video(vid, message=f"Dựng cảnh {i + 1}/{n}: AI đang làm chuyển động thật (1-5 phút)...")
                try:
                    out_m = wd / f"motion_{i:02d}_{sc['seed']}.mp4"
                    pro_api.image_to_video(absp(sc["ai_path"]), motion_prompt(sc["visual"], handheld),
                                           clip_len, motion_model, out_m)
                    sc["motion_src"], msrc = rel(out_m), out_m
                    paid += pro_api.video_price(motion_model, clip_len)
                    sc.pop("clip_path", None)
                except QuotaExceeded as e:
                    motion_off = True
                    warnings.append(f"Chuyển động AI: {e}")
                except ProviderError as e:
                    warnings.append(f"Chuyển động AI: {e}")
            if not sc.get("clip_path") or sc.get("clip_len") != clip_len:
                clip = wd / f"clip_{i:02d}.mp4"
                if msrc and msrc.exists():
                    vg.animate_video(msrc, absp(sc.get("overlay_path")), clip_len, clip)
                else:
                    vg.animate(absp(sc["frame_path"]), absp(sc.get("overlay_path")),
                               glare if sc.get("glare") else None, sc["motion"], clip_len, clip)
                sc["clip_path"], sc["clip_len"] = rel(clip), clip_len
            sc["slot"] = slots[i]
            db.update("videos", vid, scenes_json=scenes)

        if shots:
            warnings += shots.warnings
            paid += shots.cost

        # ---- audio + subtitles
        narration = ass = srt = None
        if not silent:
            _set_video(vid, progress=82, message="Ghép giọng đọc & phụ đề...")
            narration = VoiceGenerator.build_narration(wd, slots, wd / "narration.wav")
            starts = [sum(slots[:i]) for i in range(n)]
            subs = SubtitleGenerator()
            cues = subs.cues([s["text"] for s in scenes], starts, [s["speech"] for s in scenes])
            srt = subs.write_srt(cues, wd / "subtitles.srt")
            ass = subs.write_ass(cues, wd / "subs.ass")

        # ---- final
        _set_video(vid, progress=88, message="Xuất video MP4...")
        music = MUSIC_DIR / cfg["music"] if cfg.get("music") and (MUSIC_DIR / cfg["music"]).exists() else None
        out = wd / f"tiktok_{vid}.mp4"
        VideoComposer().compose(wd, [absp(s["clip_path"]) for s in scenes], slots,
                                [s["transition"] for s in scenes], narration, ass, out, music,
                                float(cfg["voice_volume"]), float(cfg["music_volume"]))
        thumb = VideoComposer.thumbnail(out, wd / "thumb.jpg", min(1.5, sum(slots) / 2))
        msg = "Hoàn thành" + (f" · Pollinations ≈ {paid:.3f} pollen" if paid else "")
        uniq = sorted(set(warnings))
        if uniq:
            msg += " (một số bước dùng phương án dự phòng: " + "; ".join(uniq)[:600] + ")"
        _set_video(vid, status="done", progress=100, message=msg, scenes_json=scenes,
                   output_path=rel(out), thumb_path=rel(thumb), srt_path=rel(srt) if srt else None, duration=round(sum(slots), 2))
    except Exception as e:
        traceback.print_exc()
        _set_video(vid, status="error", message=f"Lỗi: {e}"[:1500])


def regenerate_scene(vid: int, idx: int, text: str | None, bg_prompt: str | None,
                     new_background: bool = True, image_index: int | None = None, motion: str | None = None) -> None:
    _set_video(vid, status="queued", message=f"Chờ tạo lại cảnh {idx + 1}...")
    submit(f"regen-{vid}-{idx}", render_video, vid,
           {"idx": idx, "text": text, "bg_prompt": bg_prompt, "new_background": new_background,
            "image_index": image_index, "motion": motion})


def rerender(vid: int, fresh: bool = True) -> None:
    if fresh:
        shutil.rmtree(OUTPUTS / f"video_{vid}", ignore_errors=True)
        db.update("videos", vid, scenes_json=None, output_path=None, thumb_path=None)
    _set_video(vid, status="queued", progress=0, message="Đang chờ dựng lại...")
    submit(f"render-{vid}", render_video, vid)


def delete_video(vid: int) -> None:
    shutil.rmtree(OUTPUTS / f"video_{vid}", ignore_errors=True)
    db.execute("DELETE FROM videos WHERE id=?", (vid,))


def delete_product(pid: int) -> None:
    for v in db.query("SELECT id FROM videos WHERE product_id=?", (pid,)):
        delete_video(v["id"])
    shutil.rmtree(UPLOADS / f"p{pid}", ignore_errors=True)
    db.execute("DELETE FROM product_images WHERE product_id=?", (pid,))
    db.execute("DELETE FROM scripts WHERE product_id=?", (pid,))
    db.execute("DELETE FROM trends WHERE product_id=?", (pid,))
    db.execute("DELETE FROM products WHERE id=?", (pid,))
