"""BackgroundRemover: isolate the REAL main product from any photo (local, CPU).

Works with messy marketplace photos (Shopee/TikTok): busy backgrounds, hands holding the product,
price stickers / shop logos around it. Strategy:
  1. Crop to the product box that the vision model found (drops surrounding banners, stickers, props).
  2. Hand in the photo -> SAM (box + negative points on the hands) so the hand is not cut out with it.
     Without a usable SAM result the photo is shown as a framed card instead (never a floating hand).
  3. Otherwise -> rembg salient-object model on the crop.
  4. Sanity-check the mask; anything doubtful falls back to the 'card' presentation.
The product pixels themselves are never modified.
"""
from __future__ import annotations

import threading
import traceback
from pathlib import Path

from PIL import Image, ImageFilter

from ..config import REMBG_HAND_MODEL, REMBG_MODEL
from ..utils.imaging import box_to_pixels, load_any

_sessions: dict[str, object] = {}
_lock = threading.Lock()


def _session(name: str):
    with _lock:
        if name not in _sessions:
            from rembg import new_session
            _sessions[name] = new_session(name)  # downloads once into data/models
        return _sessions[name]


def _coverage(alpha: Image.Image) -> float:
    a = alpha.point(lambda v: 255 if v > 128 else 0)
    return (a.histogram()[255]) / float(a.width * a.height)


def _bbox_iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


class BackgroundRemover:
    def process(self, src: Path, out_dir: Path, image_id: int, meta: dict | None = None) -> dict:
        """Returns {"mode": cutout|card|skip, "cutout": path|None, "crop": path, "photo_ok": bool, "reason": str}."""
        meta = meta or {}
        img = load_any(src)
        img.thumbnail((1600, 1600))
        w, h = img.size
        result = {"mode": "card", "cutout": None, "crop": None, "photo_ok": False, "reason": ""}

        # A full-frame lifestyle / in-use photo can be shown as-is in "context" scenes.
        result["photo_ok"] = (meta.get("role") in ("in_use", "lifestyle") and meta.get("quality") != "poor"
                              and not meta.get("has_text_overlay"))
        if meta.get("inspected") and not meta.get("usable", True):
            result.update(mode="skip", reason=meta.get("note") or "ảnh không dùng được (bảng size / toàn chữ...)")

        # 1) crop to the product (generous margin so the cutout model sees edges)
        box = box_to_pixels(meta.get("product_box"), w, h, margin=0.08)
        crop = img.crop(box) if box else img
        crop_path = out_dir / f"crop_{image_id}.{'png' if crop.mode == 'RGBA' else 'jpg'}"
        crop.save(crop_path, **({} if crop.mode == "RGBA" else {"quality": 93}))
        result["crop"] = crop_path
        if result["mode"] == "skip":
            return result

        # already transparent PNG from the user: trust it
        if crop.mode == "RGBA" and crop.split()[-1].getextrema()[0] < 250:
            return self._finish(crop, out_dir, image_id, result, "PNG đã tách nền sẵn")

        # inner box = the tight product box expressed in crop coordinates
        tight = box_to_pixels(meta.get("product_box"), w, h, margin=0.0)
        if tight and box:
            tight = (tight[0] - box[0], tight[1] - box[1], tight[2] - box[0], tight[3] - box[1])

        try:
            if meta.get("has_hand"):
                cut = self._sam(crop, tight, meta.get("hand_boxes") or [], w, h, box)
                if cut is None:
                    result["reason"] = "có tay cầm — giữ ảnh gốc dạng thẻ"
                    return result
                return self._finish(cut, out_dir, image_id, result, "tách bằng SAM (bỏ tay)", tight)
            from rembg import remove
            cut = remove(crop.convert("RGB"), session=_session(REMBG_MODEL), post_process_mask=True)
            return self._finish(cut, out_dir, image_id, result, "tách nền tự động", tight)
        except Exception as e:
            traceback.print_exc()
            result["reason"] = f"tách nền lỗi ({str(e)[:80]}) — giữ ảnh gốc"
            return result

    def _sam(self, crop, tight, hand_boxes, w, h, box):
        """Segment with SAM: product box as positive prompt, hand centres as negative points."""
        if not REMBG_HAND_MODEL or not tight:
            return None
        from rembg import remove
        prompt = [{"type": "rectangle", "data": list(tight), "label": 1}]
        ox, oy = (box[0], box[1]) if box else (0, 0)
        for hb in hand_boxes:
            px = box_to_pixels(hb, w, h)
            if not px:
                continue
            cx, cy = (px[0] + px[2]) / 2 - ox, (px[1] + px[3]) / 2 - oy
            inside_product = tight[0] + (tight[2] - tight[0]) * 0.2 < cx < tight[2] - (tight[2] - tight[0]) * 0.2 \
                and tight[1] + (tight[3] - tight[1]) * 0.2 < cy < tight[3] - (tight[3] - tight[1]) * 0.2
            # a negative point in the middle of the product would cut the product itself — skip those
            if 0 <= cx < crop.width and 0 <= cy < crop.height and not inside_product:
                prompt.append({"type": "point", "data": [int(cx), int(cy)], "label": 0})
        try:
            return remove(crop.convert("RGB"), session=_session(REMBG_HAND_MODEL), sam_prompt=prompt,
                          post_process_mask=True)
        except Exception:
            traceback.print_exc()
            return None

    def _finish(self, cut: Image.Image, out_dir: Path, image_id: int, result: dict, how: str,
                tight=None) -> dict:
        cut = cut.convert("RGBA")
        alpha = cut.split()[-1]
        cov = _coverage(alpha)
        bbox = alpha.point(lambda a: 255 if a > 20 else 0).getbbox()
        problems = []
        if not bbox:
            problems.append("không tìm thấy vật thể")
        elif cov < 0.05:
            problems.append("vùng sản phẩm quá nhỏ")
        elif cov > 0.97:
            problems.append("không tách được nền")
        elif tight and _bbox_iou(bbox, tight) < 0.45:
            problems.append("vùng tách lệch khỏi sản phẩm")
        if not problems:
            # many disconnected blobs = background clutter was kept
            big = alpha.filter(ImageFilter.MaxFilter(5)).point(lambda a: 255 if a > 128 else 0)
            if _coverage(big) > cov * 2.6:
                problems.append("mask bị vụn")
        if problems:
            result["reason"] = f"{how}: {', '.join(problems)} — giữ ảnh gốc dạng thẻ"
            return result
        cut = cut.crop(bbox)
        path = out_dir / f"cut_{image_id}.png"
        cut.save(path)
        result.update(mode="cutout", cutout=path, reason=how)
        return result

    # backwards-compatible helper used by tests
    def cutout(self, src: Path, out_path: Path) -> tuple[Path, bool]:
        r = self.process(src, out_path.parent, 0, {})
        if r["cutout"]:
            Path(r["cutout"]).replace(out_path)
            return out_path, True
        return src, False
