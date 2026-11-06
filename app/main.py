"""FastAPI web app. Start with run.bat (or: python -m uvicorn app.main:app --port 8000)."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, pipeline, usage
from .config import (BG_PROVIDERS, FONT_PATH, GEMINI_API_KEY, GEMINI_IMAGE_MODEL, GEMINI_MODEL, OUTPUTS,
                     UPLOADS)
from .modules.product_analyzer import normalize as normalize_analysis
from .utils.imaging import ACCEPT_EXT, load_any
from .prompts import CATEGORIES, STYLES, UNKNOWN
from .modules.scene_planner import MOTIONS
from .utils import ffmpeg

BASE = Path(__file__).resolve().parent
app = FastAPI(title="AI TikTok Affiliate Video Generator")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
# Only uploads and rendered outputs are served — never the database or model files.
app.mount("/media/uploads", StaticFiles(directory=UPLOADS), name="media_uploads")
app.mount("/media/outputs", StaticFiles(directory=OUTPUTS), name="media_outputs")
templates = Jinja2Templates(directory=BASE / "templates")

STYLE_LABELS = {"review": "Review", "ad": "Quảng cáo", "cinematic": "Cinematic", "lifestyle": "Lifestyle"}
ALL_MOTIONS = sorted({m for ms in MOTIONS.values() for m in ms})
templates.env.globals.update(STYLE_LABELS=STYLE_LABELS, CATEGORIES=CATEGORIES, UNKNOWN=UNKNOWN,
                             ALL_MOTIONS=ALL_MOTIONS)


def _paid_info() -> dict:
    """Estimated pollen per video for the paid options shown in the render form."""
    from .config import AI_IMAGES_PER_VIDEO, AI_MOTION_SCENES, AI_RETRIES, POLLINATIONS_EDIT_MODEL, VIDEO_MODELS
    from .providers import pollinations_ai as pro_api
    img = pro_api.edit_price() * AI_IMAGES_PER_VIDEO
    return {"available": pro_api.available(), "edit_model": POLLINATIONS_EDIT_MODEL,
            "image_cost": round(img, 3), "image_cost_max": round(img * (1 + AI_RETRIES), 3),
            "motion_scenes": AI_MOTION_SCENES,
            "motion": {m: round(AI_MOTION_SCENES * pro_api.video_price(m, 4), 3) for m in VIDEO_MODELS}}


templates.env.globals["paid_info"] = _paid_info
templates.env.filters["prettyjson"] = lambda d: json.dumps(d, ensure_ascii=False, indent=2)
# links coming from web search results: only allow http(s) in href
templates.env.filters["safe_url"] = lambda u: u if isinstance(u, str) and u.startswith(("http://", "https://")) else "#"


@app.on_event("startup")
def _startup() -> None:
    db.init()


def page(request: Request, name: str, **ctx):
    ctx.setdefault("active", name.split(".")[0])
    return templates.TemplateResponse(request, name, ctx)


def _cfg_from_form(duration, voice, style, versions, music, voice_volume, music_volume, local_only,
                   image_mode="ai", ai_motion="") -> dict:
    from .config import VIDEO_MODELS
    image_mode = image_mode if image_mode in ("ai", "ai_pro", "composite") else "ai"
    return {
        "image_mode": image_mode,
        # paid AI motion: only an explicit choice in the form (the estimated cost is shown next to it)
        "ai_motion": ai_motion if ai_motion in VIDEO_MODELS and image_mode != "composite" else "",
        "duration": int(duration) if str(duration) in ("15", "30", "45") else 30,
        "voice": voice if voice in ("female", "male", "none") else "female",
        "style": style if style in STYLES else "review",
        "versions": max(1, min(3, int(versions or 1))),
        "music": music if music in pipeline.music_files() else "",
        "voice_volume": max(0.0, min(2.0, float(voice_volume or 1.0))),
        "music_volume": max(0.0, min(1.0, float(music_volume or 0.15))),
        "local_only": bool(local_only),
    }


def _defaults(p: dict, scripts: list[dict]) -> dict:
    """Pre-selected form values: what the user chose last time for this product."""
    d = dict(pipeline.DEFAULT_CONFIG)
    if scripts:  # newest scripts were written for this duration/style
        d.update(duration=scripts[0].get("duration") or d["duration"], style=scripts[0].get("style") or d["style"])
    d.update({k: v for k, v in (db.loads(p.get("last_config"), {}) or {}).items() if v is not None})
    # paid options are never pre-selected: the user picks them again each time (cost approval)
    d["ai_motion"] = ""
    if d.get("image_mode") == "ai_pro":
        d["image_mode"] = "ai"
    return d


def _quick(style: str, duration: str) -> dict:
    return {"style": style if style in STYLES else "review",
            "duration": int(duration) if duration in ("15", "30", "45") else 30}


def _remember(pid: int, changes: dict) -> None:
    p = db.one("SELECT last_config FROM products WHERE id=?", (pid,))
    if p is not None:
        db.update("products", pid, last_config={**(db.loads(p["last_config"], {}) or {}), **changes})


# ------------------------------------------------------------------ pages
@app.get("/")
def dashboard(request: Request):
    stats = {
        "products": db.one("SELECT COUNT(*) n FROM products")["n"],
        "videos_done": db.one("SELECT COUNT(*) n FROM videos WHERE status='done'")["n"],
        "videos_today": db.one("SELECT COUNT(*) n FROM videos WHERE status='done' AND created_at LIKE ?",
                               (db.now()[:10] + "%",))["n"],
        "scripts": db.one("SELECT COUNT(*) n FROM scripts")["n"],
    }
    recent = db.query("SELECT v.*, p.name pname FROM videos v JOIN products p ON p.id=v.product_id "
                      "ORDER BY v.id DESC LIMIT 6")
    system = {
        "ffmpeg": ffmpeg.version(), "font": FONT_PATH, "gemini": bool(GEMINI_API_KEY), "gemini_model": GEMINI_MODEL,
        "gemini_image": GEMINI_IMAGE_MODEL, "bg_providers": BG_PROVIDERS, "queue": pipeline.pending_jobs(),
        "pollen": __import__("app.providers.pollinations_ai", fromlist=["balance"]).balance(),
    }
    return page(request, "dashboard.html", stats=stats, recent=recent, system=system,
                usage_today=usage.summary_today(), usage_all=usage.summary_all(), usage_recent=usage.recent(15))


@app.get("/create")
def create_page(request: Request):
    return page(request, "create.html", music=pipeline.music_files(), has_key=bool(GEMINI_API_KEY),
                accept="image/*," + ",".join(ACCEPT_EXT))


@app.post("/create")
async def create_submit(
    request: Request,
    images: list[UploadFile] = File(...),
    name: str = Form(""), price: str = Form(""), link: str = Form(""), user_info: str = Form(""),
    category: str = Form("auto"), duration: str = Form("30"), voice: str = Form("female"),
    style: str = Form("review"), versions: str = Form("1"), music: str = Form(""),
    voice_volume: str = Form("1.0"), music_volume: str = Form("0.15"),
    local_only: str = Form(""), review_first: str = Form(""), ref_links: str = Form(""),
    image_mode: str = Form("ai"), ai_motion: str = Form(""),
):
    files = [f for f in images if f.filename][:10]
    if not files:
        raise HTTPException(400, "Cần ít nhất 1 ảnh sản phẩm")
    cfg = _cfg_from_form(duration, voice, style, versions, music, voice_volume, music_volume, local_only,
                         image_mode, ai_motion)
    pid = db.insert("products", name=name.strip(), price=price.strip(), link=link.strip(),
                    user_info=user_info.strip(), category=category if category in CATEGORIES else "auto",
                    ref_links=ref_links.strip(), last_config=cfg, status="queued", message="Đang chờ xử lý...", created_at=db.now(), updated_at=db.now())
    folder = UPLOADS / f"p{pid}"
    folder.mkdir(parents=True, exist_ok=True)
    skipped, seen = [], set()
    for f in files:
        raw = await f.read()
        if len(raw) > 30 * 1024 * 1024:
            skipped.append(f"{f.filename} (quá 30MB)")
            continue
        digest = hashlib.md5(raw).hexdigest()
        if digest in seen:  # same photo uploaded twice
            continue
        try:
            img = load_any(raw)  # format detected from content: jpg/jfif/png/webp/avif/heic/gif/bmp/tiff
            img.load()
        except Exception:
            skipped.append(f"{f.filename} (không đọc được định dạng)")
            continue
        if min(img.size) < 120:
            skipped.append(f"{f.filename} (ảnh quá nhỏ)")
            continue
        img.thumbnail((2000, 2000))  # keeps disk usage small
        i = len(seen)
        seen.add(digest)
        keep_alpha = img.mode == "RGBA"
        out = folder / f"img_{i}.{'png' if keep_alpha else 'jpg'}"
        img.save(out, **({} if keep_alpha else {"quality": 92}))
        db.insert("product_images", product_id=pid, path=pipeline.rel(out), position=i)
    if not pipeline.product_images(pid):
        pipeline.delete_product(pid)
        raise HTTPException(400, "Không đọc được ảnh nào. Bỏ qua: " + "; ".join(skipped))
    if skipped:
        db.update("products", pid, message="Bỏ qua ảnh: " + "; ".join(skipped))
    if review_first:
        pipeline.submit(f"prepare-{pid}", pipeline.job_prepare, pid, cfg["style"], cfg["duration"])
    else:
        pipeline.submit(f"auto-{pid}", pipeline.job_auto, pid, cfg)
    return RedirectResponse(f"/products/{pid}", status_code=303)


@app.get("/products")
def products_page(request: Request):
    rows = db.query("""SELECT p.*, (SELECT path FROM product_images WHERE product_id=p.id ORDER BY position LIMIT 1) img,
                       (SELECT COUNT(*) FROM videos WHERE product_id=p.id AND status='done') nvideos
                       FROM products p ORDER BY p.id DESC""")
    return page(request, "products.html", products=rows)


@app.get("/products/{pid}")
def product_page(request: Request, pid: int):
    p = db.one("SELECT * FROM products WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404)
    scripts = db.query("SELECT * FROM scripts WHERE product_id=? ORDER BY id DESC", (pid,))
    for s in scripts:
        s["data"] = db.loads(s["data_json"], {})
    videos = db.query("SELECT * FROM videos WHERE product_id=? ORDER BY id DESC", (pid,))
    return page(request, "product.html", active="products", p=p, analysis=db.loads(p["analysis_json"]),
                trends=pipeline.latest_trends(pid), defaults=_defaults(p, scripts),
                images=pipeline.product_images(pid), scripts=scripts, videos=videos,
                music=pipeline.music_files())


@app.post("/products/{pid}/edit")
def product_edit(pid: int, name: str = Form(""), price: str = Form(""), link: str = Form(""),
                 user_info: str = Form(""), category: str = Form("auto"), ref_links: str = Form("")):
    db.update("products", pid, name=name.strip(), price=price.strip(), link=link.strip(), ref_links=ref_links.strip(),
              user_info=user_info.strip(), category=category if category in CATEGORIES else "auto",
              updated_at=db.now())
    return RedirectResponse(f"/products/{pid}", status_code=303)


@app.post("/products/{pid}/analyze")
def product_analyze(pid: int, style: str = Form("review"), duration: str = Form("30")):
    db.update("products", pid, status="queued", message="Đang chờ phân tích lại...")
    _remember(pid, _quick(style, duration))
    pipeline.submit(f"prepare-{pid}", pipeline.job_prepare, pid, style if style in STYLES else "review",
                    int(duration) if duration in ("15", "30", "45") else 30, True)
    return RedirectResponse(f"/products/{pid}", status_code=303)


@app.post("/products/{pid}/analysis")
def product_analysis_save(pid: int, analysis_json: str = Form(...)):
    try:
        data = json.loads(analysis_json)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"JSON không hợp lệ: {e}")
    p = db.one("SELECT * FROM products WHERE id=?", (pid,))
    if not p or not isinstance(data, dict):
        raise HTTPException(400, "Phân tích phải là một object JSON")
    data = normalize_analysis(data, pipeline.product_info(p), len(pipeline.product_images(pid)))
    db.update("products", pid, analysis_json=data, analysis_source="user-edited", updated_at=db.now())
    for r in pipeline.product_images(pid):  # edited product boxes -> redo cutouts
        db.update("product_images", r["id"], cutout_path=None, crop_path=None, mode=None, reason=None)
    return RedirectResponse(f"/products/{pid}#analysis", status_code=303)


@app.post("/products/{pid}/scripts")
def product_new_scripts(pid: int, style: str = Form("review"), duration: str = Form("30")):
    db.update("products", pid, status="queued", message="Đang chờ viết kịch bản...")
    _remember(pid, _quick(style, duration))
    pipeline.submit(f"prepare-{pid}", pipeline.job_prepare, pid, style if style in STYLES else "review",
                    int(duration) if duration in ("15", "30", "45") else 30)
    return RedirectResponse(f"/products/{pid}#scripts", status_code=303)


@app.post("/products/{pid}/trends")
def product_trends(pid: int, style: str = Form("review"), duration: str = Form("30")):
    """Refresh trend research, then write 3 new scripts based on it."""
    db.update("products", pid, status="queued", message="Đang chờ nghiên cứu xu hướng...")
    _remember(pid, _quick(style, duration))
    pipeline.submit(f"prepare-{pid}", pipeline.job_prepare, pid, style if style in STYLES else "review",
                    int(duration) if duration in ("15", "30", "45") else 30, False, True)
    return RedirectResponse(f"/products/{pid}#trends", status_code=303)


@app.post("/products/{pid}/delete")
def product_delete(pid: int):
    pipeline.delete_product(pid)
    return RedirectResponse("/products", status_code=303)


@app.get("/scripts")
def scripts_page(request: Request):
    rows = db.query("SELECT s.*, p.name pname FROM scripts s JOIN products p ON p.id=s.product_id ORDER BY s.id DESC")
    for s in rows:
        s["data"] = db.loads(s["data_json"], {})
    return page(request, "scripts.html", scripts=rows)


@app.post("/scripts/{sid}")
async def script_save(request: Request, sid: int):
    s = db.one("SELECT * FROM scripts WHERE id=?", (sid,))
    if not s:
        raise HTTPException(404)
    form = await request.form()
    data = db.loads(s["data_json"], {})
    data["title"] = str(form.get("title", data.get("title", ""))).strip()
    scenes = []
    i = 0
    while f"text_{i}" in form:
        text = re.sub(r"\s+", " ", str(form.get(f"text_{i}", ""))).strip()
        if text:
            scenes.append({"text": text, "visual": str(form.get(f"visual_{i}", "context")),
                           "background_prompt": str(form.get(f"bg_{i}", "")).strip()})
        i += 1
    if scenes:
        data["scenes"] = scenes
        data["hook"], data["cta"] = scenes[0]["text"], scenes[-1]["text"]
    db.update("scripts", sid, data_json=data, updated_at=db.now())
    return RedirectResponse(f"/products/{s['product_id']}#script-{sid}", status_code=303)


@app.post("/scripts/{sid}/render")
def script_render(sid: int, duration: str = Form("30"), voice: str = Form("female"), style: str = Form("review"),
                  music: str = Form(""), voice_volume: str = Form("1.0"), music_volume: str = Form("0.15"),
                  local_only: str = Form(""), image_mode: str = Form("ai"), ai_motion: str = Form("")):
    s = db.one("SELECT * FROM scripts WHERE id=?", (sid,))
    if not s:
        raise HTTPException(404)
    cfg = _cfg_from_form(duration, voice, style, 1, music, voice_volume, music_volume, local_only, image_mode,
                         ai_motion)
    _remember(s["product_id"], cfg)
    vid = pipeline.create_video(s["product_id"], sid, cfg)
    return RedirectResponse(f"/videos/{vid}", status_code=303)


@app.post("/scripts/{sid}/delete")
def script_delete(sid: int):
    s = db.one("SELECT product_id FROM scripts WHERE id=?", (sid,))
    db.execute("DELETE FROM scripts WHERE id=?", (sid,))
    return RedirectResponse(f"/products/{s['product_id']}#scripts" if s else "/scripts", status_code=303)


@app.get("/videos/{vid}")
def video_page(request: Request, vid: int):
    v = db.one("SELECT v.*, p.name pname FROM videos v JOIN products p ON p.id=v.product_id WHERE v.id=?", (vid,))
    if not v:
        raise HTTPException(404)
    srow = db.one("SELECT data_json FROM scripts WHERE id=?", (v["script_id"],)) if v["script_id"] else None
    return page(request, "video.html", active="history", v=v, scenes=db.loads(v["scenes_json"], []),
                script=db.loads(srow["data_json"], {}) if srow else {},
                cfg=db.loads(v["config_json"], {}),
                images=pipeline.prepare_images(v["product_id"], process=False))


@app.post("/videos/{vid}/scenes/{idx}")
def video_scene_regen(vid: int, idx: int, text: str = Form(""), bg_prompt: str = Form(""),
                      new_background: str = Form(""), image_index: str = Form(""), motion: str = Form("")):
    pipeline.regenerate_scene(vid, idx, text or None, bg_prompt or None, bool(new_background),
                              int(image_index) if image_index.isdigit() else None,
                              motion if motion in ALL_MOTIONS else None)
    return RedirectResponse(f"/videos/{vid}", status_code=303)


@app.post("/videos/{vid}/rerender")
def video_rerender(vid: int):
    pipeline.rerender(vid, fresh=True)
    return RedirectResponse(f"/videos/{vid}", status_code=303)


@app.post("/videos/{vid}/delete")
def video_delete(vid: int):
    pipeline.delete_video(vid)
    return RedirectResponse("/history", status_code=303)


@app.get("/videos/{vid}/download")
def video_download(vid: int):
    v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
    if not v or not v["output_path"]:
        raise HTTPException(404)
    return FileResponse(pipeline.absp(v["output_path"]), media_type="video/mp4", filename=f"tiktok_{vid}.mp4")


@app.get("/history")
def history_page(request: Request):
    rows = db.query("SELECT v.*, p.name pname FROM videos v JOIN products p ON p.id=v.product_id ORDER BY v.id DESC")
    return page(request, "history.html", videos=rows)


# ------------------------------------------------------------------ JSON status (polled by the UI)
@app.get("/api/videos/{vid}")
def api_video(vid: int):
    v = db.one("SELECT id, status, progress, message, output_path FROM videos WHERE id=?", (vid,))
    return JSONResponse(v or {"status": "missing"})


@app.get("/api/products/{pid}")
def api_product(pid: int):
    p = db.one("SELECT id, status, message FROM products WHERE id=?", (pid,))
    vids = db.query("SELECT id, status, progress, message FROM videos WHERE product_id=? ORDER BY id DESC", (pid,))
    nscripts = db.one("SELECT COUNT(*) n FROM scripts WHERE product_id=?", (pid,))["n"]
    return JSONResponse({**(p or {}), "videos": vids, "scripts": nscripts})
