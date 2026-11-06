"""AI product shots for one video: pick the best reference photos, generate (free HF FLUX Kontext, or paid
Pollinations multi-reference models when the user chose "AI Pro"), let Gemini check the result against the
real product and redo it once if it drifted.

Consistency rule: the first shot is the ANCHOR (made from the real photos). Later shots are made from the
anchor (+ the real photos for multi-reference models) with only the camera changed, so the device, hand,
background and light stay the same in every scene.
"""
from __future__ import annotations

from pathlib import Path

from .. import providers
from ..config import AI_RETRIES, AI_VERIFY, AI_VERIFY_MIN, POLLINATIONS_EDIT_MODEL
from ..providers import pollinations_ai as pro_api
from ..providers.base import ProviderError, QuotaExceeded
from ..providers.hf_edit import HFProductScene, follow_prompt, pro_prompt, scene_prompt

_ROLE = {"front": 3, "back": 3, "side": 2, "detail": 1, "packaging": 1, "in_use": 0, "lifestyle": 0}
_QUALITY = {"good": 3, "ok": 1, "poor": -3}


def _abs(r):
    from ..config import DATA
    return (DATA / r) if r else None


def rank_refs(imgs: list[dict]) -> list[Path]:
    """Real photos best suited as AI reference: clear, product facing the camera, no hands/overlays."""
    def score(im):
        m = im.get("meta") or {}
        s = _ROLE.get(m.get("role", "other"), 0) + _QUALITY.get(m.get("quality", ""), 0)
        s -= 3 * bool(m.get("has_hand")) + 2 * bool(m.get("has_text_overlay"))
        s -= 1 if m.get("background") == "busy" else 0
        s += 1 if im.get("mode") == "cutout" else 0
        s += 0.5 if im is imgs[0] else 0  # analysis' main photo
        return s
    out = []
    for im in sorted(imgs, key=score, reverse=True):
        p = _abs(im.get("crop_path"))
        p = p if p and p.exists() else _abs(im["path"])
        if p and p.exists() and p not in out:
            out.append(p)
    return out


class AIShots:
    def __init__(self, pro: bool, staging: str, handheld: bool, props: str, refs: list[Path]):
        self.pro = pro and pro_api.available()
        self.staging, self.handheld, self.props, self.refs = staging, handheld, (props or "").rstrip(". "), refs
        self.hf = HFProductScene()
        self.hf_off = self.pro_off = False
        self.failures = 0
        self.verify_off = not AI_VERIFY
        self.warnings: list[str] = []
        self.cost = 0.0
        if pro and not self.pro:
            self.warnings.append("AI Pro: chưa có POLLINATIONS_TOKEN (sk_...) → dùng FLUX Kontext miễn phí")

    @property
    def off(self) -> bool:
        return (self.hf_off or self.failures >= 2) and (not self.pro or self.pro_off)

    # ---------------------------------------------------------------- generation
    def _one(self, visual, out, seed, anchor, own_setting, video_setting, own_ref) -> tuple[Path, str] | None:
        product_refs = [own_ref] if own_ref else self.refs[:2]
        if self.pro and not self.pro_off:
            refs = product_refs + ([anchor] if anchor else [])
            prompt = pro_prompt(visual, len(product_refs), bool(anchor), own_setting or video_setting,
                                self.staging, self.handheld, self.props)
            try:
                pro_api.edit_image(refs, prompt, out, seed)
                self.cost += pro_api.edit_price()
                return out, f"AI Pro ({POLLINATIONS_EDIT_MODEL})"
            except QuotaExceeded as e:
                self.pro_off = True
                self.warnings.append(f"AI Pro: {e} → dùng FLUX Kontext miễn phí")
            except ProviderError as e:
                self.warnings.append(f"AI Pro: {e}")
        if self.hf_off or self.failures >= 2:
            return None
        if anchor:
            ref, prompt = anchor, follow_prompt(visual, self.props, self.handheld, own_setting)
        else:
            ref = own_ref or self.refs[0]
            prompt = scene_prompt(visual, own_setting or video_setting, self.staging, self.handheld, self.props)
        try:
            self.hf.generate(ref, prompt, seed % 2_147_483_647, out)
            return out, "AI tái tạo (FLUX Kontext)"
        except QuotaExceeded as e:
            self.hf_off = True
            self.warnings.append(f"AI tạo ảnh: {e}")
        except ProviderError as e:
            self.failures += 1
            self.warnings.append(f"AI tạo ảnh: {e}")
        return None

    def _check(self, candidate: Path, anchor: Path | None, product: Path) -> tuple[float, str]:
        """Gemini score (0-10, the weakest of product / scene / realism). 10 = not checked."""
        if self.verify_off:
            return 10.0, ""
        llm = next((p for p in providers.llm_chain() if hasattr(p, "verify_image")), None)
        if not llm:
            self.verify_off = True
            return 10.0, ""
        try:
            d = llm.verify_image(product, candidate, anchor)
        except providers.ProviderError:
            self.verify_off = True  # Gemini quota etc.: keep going without checks
            return 10.0, ""
        keys = ["product", "real"] + (["scene"] if anchor else [])
        try:
            score = min(float(d.get(k, 0) or 0) for k in keys)
        except (TypeError, ValueError):
            score = 0.0
        return score, str(d.get("issues", ""))[:120]

    def make(self, visual: str, out: Path, seed: int, anchor: Path | None, own_setting: str,
             video_setting: str, own_ref: Path | None) -> tuple[Path, str] | None:
        product = own_ref or (self.refs[0] if self.refs else None)
        if product is None:
            return None
        best: tuple[float, Path, str, str] | None = None
        for attempt in range(1 + AI_RETRIES):
            target = out if attempt == 0 else out.with_name(f"{out.stem}_r{attempt}{out.suffix}")
            res = self._one(visual, target, seed + 7919 * attempt, anchor, own_setting, video_setting, own_ref)
            if not res:
                break
            score, issues = self._check(res[0], anchor, product)
            if best is None or score > best[0]:
                best = (score, res[0], res[1], issues)
            if score >= AI_VERIFY_MIN:
                break
        if not best:
            return None
        score, path, label, issues = best
        if score < AI_VERIFY_MIN:
            self.warnings.append(f"Gemini chấm ảnh AI {score:.0f}/10 ({issues or 'lệch sản phẩm'})")
            if score < 4:
                return None  # clearly wrong product: better the real photo / anchor than a fake
        if path != out:
            path.replace(out)
        return out, label + ("" if self.verify_off else f" · Gemini {score:.0f}/10")


_MOVES = {
    "intro": "slow smooth camera push-in toward the product",
    "context": "gentle handheld camera drift, natural everyday movement",
    "benefit": "slow camera orbit of about 15 degrees around the product",
    "closeup": "very slow push-in on the product details",
    "cta": "slow pull-back",
}


def motion_prompt(visual: str, handheld: bool) -> str:
    hand = ("The hand moves naturally and slowly tilts the product a little (under 20 degrees), five normal fingers. "
            if handheld else "The product stays still. ")
    return (f"{_MOVES.get(visual, _MOVES['intro'])}. {hand}Background stays the same, soft natural light. "
            "The product's shape, colors, printed design and text stay exactly the same; never show its other side. "
            "No text, no new objects, realistic.")


def motion_scenes(scenes: list[dict], k: int) -> set[int]:
    """Which scenes get a real AI clip: hook first, then lifestyle / benefit shots."""
    pref = ["intro", "context", "benefit", "closeup", "cta"]
    order = sorted(range(len(scenes)), key=lambda i: (pref.index(scenes[i]["visual"])
                                                       if scenes[i]["visual"] in pref else 9, i))
    return set(order[:k])


def motion_estimate(scenes_or_n, model: str, seconds_each: float = 4.0) -> float:
    from ..config import AI_MOTION_SCENES, VIDEO_MODELS
    if model not in VIDEO_MODELS:
        return 0.0
    return round(AI_MOTION_SCENES * pro_api.video_price(model, seconds_each), 3)
