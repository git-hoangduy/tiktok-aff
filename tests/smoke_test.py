"""End-to-end smoke test. Run:  .venv\\Scripts\\python -m tests.smoke_test [--online]

Without --online it forces the local/offline providers (template scripts, procedural backgrounds);
voice still needs Internet (Edge TTS). With --online it uses whatever is configured in .env."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, pipeline  # noqa: E402
from app.config import FFPROBE, UPLOADS  # noqa: E402
from app.modules.background_remover import BackgroundRemover  # noqa: E402
from app.modules.subtitle_generator import SubtitleGenerator  # noqa: E402
from app.utils.imaging import font  # noqa: E402

ONLINE = "--online" in sys.argv


def sample_product(path: Path) -> Path:
    """A fake serum bottle on a white table photo."""
    img = Image.new("RGB", (900, 1200), (246, 244, 240))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([330, 380, 570, 1040], radius=50, fill=(232, 120, 150))
    d.rectangle([395, 250, 505, 385], fill=(40, 40, 48))
    d.rounded_rectangle([370, 560, 530, 820], radius=16, fill=(255, 255, 255))
    d.text((392, 620), "GLOW", font=font(52), fill=(200, 60, 100))
    d.text((390, 700), "Serum 30ml", font=font(28), fill=(60, 60, 60))
    img.save(path)
    return path


def check(name: str, ok: bool, extra: str = "") -> None:
    print(f"[{'OK' if ok else 'FAIL'}] {name} {extra}")
    if not ok:
        sys.exit(1)


def main() -> None:
    db.init()
    if not ONLINE:
        import app.providers as P
        P.gemini_available = lambda: False  # force template LLM
    t0 = time.time()
    pid = db.insert("products", name="Serum Glow 30ml", price="199.000đ", link="",
                    user_info="Dưỡng ẩm cho da. Thấm nhanh, không bết dính. Chai 30ml nhỏ gọn.",
                    category="cosmetics", status="new", created_at=db.now(), updated_at=db.now())
    folder = UPLOADS / f"p{pid}"
    folder.mkdir(parents=True, exist_ok=True)
    src = sample_product(folder / "img_0.png")
    db.insert("product_images", product_id=pid, path=pipeline.rel(src), position=0)

    # BackgroundRemover
    cut, ok = BackgroundRemover().cutout(src, folder / "test_cut.png")
    check("BackgroundRemover", ok and Image.open(cut).mode == "RGBA", str(Image.open(cut).size))

    # Analyzer + scripts
    pipeline.job_prepare(pid, "review", 15)
    p = db.one("SELECT * FROM products WHERE id=?", (pid,))
    check("ProductAnalyzer", bool(p["analysis_json"]), f"source={p['analysis_source']}")
    scripts = db.query("SELECT * FROM scripts WHERE product_id=?", (pid,))
    check("ScriptGenerator (3 scripts)", len(scripts) == 3, f"source={scripts[0]['source']}")
    print(json.dumps(db.loads(scripts[0]["data_json"]), ensure_ascii=False, indent=1)[:800])

    # Subtitles unit check
    cues = SubtitleGenerator().cues(["Xin chào mọi người, hôm nay mình review món này nhé."], [0.0], [3.0])
    check("SubtitleGenerator", abs(cues[-1][1] - 3.0) < 0.01, f"{len(cues)} cues")

    # Full render
    vid = pipeline.create_video(pid, scripts[0]["id"], {"duration": 15, "local_only": not ONLINE}, enqueue=False)
    pipeline.render_video(vid)
    v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
    check("Render", v["status"] == "done", v["message"])
    out = pipeline.absp(v["output_path"])
    info = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                                      "stream=codec_type,codec_name,width,height:format=duration",
                                      "-of", "json", str(out)], capture_output=True, text=True).stdout)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    dur = float(info["format"]["duration"])
    check("Output 1080x1920 h264 + audio", vs["width"] == 1080 and vs["height"] == 1920
          and vs["codec_name"] == "h264" and has_audio, f"{dur:.1f}s")

    # Regenerate one scene
    pipeline.render_video(vid, {"idx": 1, "text": None, "bg_prompt": None, "new_background": True})
    v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
    check("Regenerate scene 2", v["status"] == "done", v["message"])
    print(f"\nVideo: {out}\nTotal time: {time.time() - t0:.0f}s")
    if "--keep" not in sys.argv:
        pipeline.delete_product(pid)
        print("(test data removed — pass --keep to keep it)")


if __name__ == "__main__":
    main()
