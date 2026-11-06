"""ScenePlanner: script scenes -> concrete visual plan (layout, motion, overlay, photo, seed)."""
from __future__ import annotations

import random

from ..prompts import BG_SUFFIX

LAYOUT = {"intro": "hero", "closeup": "closeup", "context": "context", "benefit": "benefit", "cta": "cta"}
MOTIONS = {
    "intro": ["push_in", "zoom_in"],
    "closeup": ["zoom_in", "pan_up"],
    "context": ["pan_left", "pan_right", "zoom_out"],
    "benefit": ["zoom_in", "pan_right", "pan_left"],
    "cta": ["zoom_out", "push_in"],
}
TRANSITIONS = ["fade", "slideleft", "smoothleft", "circleopen", "wipeup", "slideup", "fadewhite"]


def short_headline(text: str, max_words: int = 7) -> str:
    words = text.replace(",", " ").split()
    head = " ".join(words[:max_words])
    return head.rstrip(".!?") + ("..." if len(words) > max_words else "")


class ScenePlanner:
    @staticmethod
    def pick_image(visual: str, i: int, images: list[dict], used: list[int]) -> tuple[int, str | None]:
        """Choose which photo a scene shows. images[0] is the main photo.
        Returns (index, layout override) — 'photo' shows a lifestyle photo full-frame."""
        n = len(images)
        if n <= 1 or visual in ("intro", "cta"):
            return 0, None

        def least_used(cands):
            return min(cands, key=lambda k: (used.count(k), k)) if cands else None

        def first(*candidate_lists):
            for c in candidate_lists:
                k = least_used(c)
                if k is not None:
                    return k
            return 0

        if visual == "closeup":
            return first([k for k, im in enumerate(images) if k and im.get("role") in ("detail", "packaging",
                                                                                       "front", "side")],
                         list(range(1, n))), None
        if visual == "context":
            k = least_used([k for k, im in enumerate(images) if im.get("photo_ok")])
            if k is not None and used.count(k) == 0:
                return k, "photo"  # real in-use photo beats a generated scene
        return first([k for k in range(n) if images[k].get("mode") == "cutout"], list(range(n))), None

    def plan(self, script: dict, n_images: int, style: str, seed: int | None = None,
             product_name: str = "", images: list[dict] | None = None) -> list[dict]:
        rnd = random.Random(seed)
        images = images or [{} for _ in range(max(1, n_images))]
        used: list[int] = []
        scenes = []
        style_hint = {"cinematic": ", cinematic moody lighting, dramatic",
                      "lifestyle": ", cozy lifestyle home atmosphere",
                      "ad": ", bright clean commercial look",
                      "review": ", natural casual home setting"}.get(style, "")
        for i, sc in enumerate(script["scenes"]):
            visual = sc.get("visual", "context")
            overlay = None
            if visual == "intro":
                # Product name as the title card (the hook itself is already in the subtitles).
                title = product_name.strip() or short_headline(script.get("hook") or sc["text"])
                overlay = {"kind": "headline", "text": short_headline(title, 6)}
            elif visual == "benefit":
                overlay = {"kind": "badge", "text": short_headline(sc["text"], 6)}
            elif visual == "cta":
                overlay = {"kind": "cta", "text": "Bấm giỏ hàng để xem ngay"}
            img_idx, layout_override = self.pick_image(visual, i, images, used)
            used.append(img_idx)
            scenes.append({
                "idx": i,
                "text": sc["text"],
                "visual": visual,
                "layout": layout_override or LAYOUT.get(visual, "context"),
                "motion": rnd.choice(MOTIONS.get(visual, ["zoom_in"])),
                "transition": TRANSITIONS[(i + rnd.randrange(len(TRANSITIONS))) % len(TRANSITIONS)],
                "image_index": img_idx,
                "bg_prompt": sc.get("background_prompt", "") + style_hint + BG_SUFFIX,
                "setting": (sc.get("background_prompt", "") + style_hint).strip(", "),  # for AI product scenes
                "overlay": overlay,
                "glare": visual in ("intro", "cta"),
                "seed": rnd.randrange(1, 10_000_000),
            })
        self.assign_ai_slots(scenes)
        return scenes

    @staticmethod
    def assign_ai_slots(scenes: list[dict], max_images: int | None = None) -> None:
        """Which AI-generated product photo each scene uses. Free GPU quota is small, so only
        `max_images` photos are made per video; the CTA reuses the hero shot, others round-robin
        (each reuse gets a different camera motion, so it still feels like a new shot)."""
        from ..config import AI_IMAGES_PER_VIDEO
        cap = max_images or AI_IMAGES_PER_VIDEO
        slot = 0
        for sc in scenes:
            if sc["visual"] == "cta" and scenes and sc is not scenes[0]:
                sc["ai_slot"] = 0
            elif slot < cap:
                sc["ai_slot"] = slot
                slot += 1
            else:
                sc["ai_slot"] = 1 + (sc["idx"] % max(1, cap - 1)) if cap > 1 else 0
