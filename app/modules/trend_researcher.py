"""TrendResearcher: collect what is trending right now (free sources) so scripts follow
current formats instead of guessing.

Sources (all free, no key):
  1. Web search (DuckDuckGo via `ddgs`): recent articles/reports about TikTok trends for the category.
  2. Video search (DuckDuckGo videos): hot TikTok/YouTube videos for the product type, with view counts.
  3. Reference links the user pastes (TikTok oEmbed: caption + author of each video).
The collected material is then summarised by the LLM into a trend report with source ids.

Limits: we read titles, captions, view counts and article text — nobody 'watches' the videos.
"""
from __future__ import annotations

import html
import re
import time
from collections import Counter
from datetime import date

import httpx

from .. import providers, usage
from ..prompts import CATEGORIES

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) aff-tiktok-local/1.0"}

CAT_VI = {"cosmetics": "mỹ phẩm skincare", "household": "đồ gia dụng", "fashion": "thời trang quần áo",
          "accessory": "phụ kiện", "food": "đồ ăn vặt thực phẩm", "electronics": "đồ công nghệ", "other": "sản phẩm"}
CAT_EN = {"cosmetics": "skincare beauty", "household": "home kitchen gadgets", "fashion": "fashion outfit",
          "accessory": "accessories", "food": "snack food", "electronics": "tech gadgets", "other": "products"}


def _clean_html(raw: str, limit: int = 1500) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|header|footer|nav)[^>]*>.*?</\1>", " ", raw)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _keyword(analysis: dict, info: dict) -> str:
    """Short product-type keyword for searches, e.g. 'serum dưỡng ẩm'."""
    name = (info.get("name") or analysis.get("product_name") or "").strip()
    cat = analysis.get("category") or ""
    base = name if name and name != "chưa xác định" else cat
    return " ".join(base.split()[:5])


class TrendResearcher:
    def __init__(self) -> None:
        self.errors: list[str] = []

    # ---------------- collection ----------------
    def _ddgs(self):
        from ddgs import DDGS
        return DDGS(timeout=20)

    def search_articles(self, queries: list[str], per_query: int = 5) -> list[dict]:
        out, seen = [], set()
        for q in queries:
            rs = []
            for tl in ("m", "y"):  # past month first, then past year
                try:
                    rs = self._ddgs().text(q, region="vn-vi", max_results=per_query, timelimit=tl)
                except Exception as e:
                    self.errors.append(f"web '{q}': {str(e)[:80]}")
                    rs = []
                if rs:
                    break
            usage.log("ddgs", "trend_web", bool(rs), q)
            for r in rs:
                if r.get("href") in seen:
                    continue
                seen.add(r.get("href"))
                out.append({"type": "article", "title": r.get("title", ""), "url": r.get("href", ""),
                            "snippet": r.get("body", ""), "query": q})
            time.sleep(0.8)
        return out

    def search_videos(self, queries: list[str], per_query: int = 10) -> list[dict]:
        out, seen = [], set()
        for q in queries:
            rs = []
            for tl in ("m", "y"):  # past month first, then past year
                try:
                    rs = self._ddgs().videos(q, region="vn-vi", max_results=per_query, timelimit=tl) or []
                except Exception as e:
                    self.errors.append(f"video '{q}' ({tl}): {str(e)[:80]}")
                    rs = []
                if rs:
                    break
            usage.log("ddgs", "trend_video", bool(rs), q)
            for v in rs:
                url = v.get("content") or v.get("embed_url") or ""
                if not url or url in seen:
                    continue
                seen.add(url)
                views = (v.get("statistics") or {}).get("viewCount")
                out.append({"type": "video", "title": v.get("title", ""), "url": url,
                            "platform": v.get("publisher", ""), "views": views,
                            "published": (v.get("published") or "")[:10], "duration": v.get("duration", ""),
                            "snippet": (v.get("description") or "")[:300], "query": q})
            time.sleep(0.8)
        # short-form platforms first, then most viewed
        short = [x for x in out if x["platform"].lower() in ("tiktok", "youtube")]
        short.sort(key=lambda x: (x["platform"].lower() != "tiktok", -(x["views"] or 0)))
        return short[:15]

    def reference_links(self, links: list[str]) -> list[dict]:
        out = []
        for url in links[:10]:
            url = url.strip()
            if not url.startswith("http"):
                continue
            item = {"type": "reference", "url": url, "title": "", "author": "", "platform": "tiktok"}
            if "tiktok.com" in url:
                try:
                    r = httpx.get("https://www.tiktok.com/oembed", params={"url": url}, headers=UA, timeout=20)
                    if r.status_code == 200:
                        j = r.json()
                        item.update(title=j.get("title", ""), author=j.get("author_name", ""))
                    usage.log("tiktok_oembed", "reference", r.status_code == 200, url[:120])
                except Exception as e:
                    self.errors.append(f"oembed {url[:50]}: {str(e)[:60]}")
            out.append(item)
        return out

    def fetch_pages(self, articles: list[dict], n: int = 3) -> None:
        """Add page text to the first n articles (more substance than search snippets)."""
        for a in articles[:n]:
            try:
                r = httpx.get(a["url"], headers=UA, timeout=httpx.Timeout(15, connect=8), follow_redirects=True)
                if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
                    a["text"] = _clean_html(r.text)
            except Exception:
                pass

    # ---------------- main entry ----------------
    def research(self, analysis: dict, info: dict, ref_links: list[str] | None = None) -> dict:
        cat = analysis.get("category_key") or "other"
        if info.get("category") in CATEGORIES:
            cat = info["category"]
        kw = _keyword(analysis, info)
        month = date.today().strftime("%m/%Y")
        year = date.today().year
        article_q = [f"xu hướng TikTok {CAT_VI.get(cat, '')} tháng {month}",
                     f"TikTok Shop Việt Nam {CAT_VI.get(cat, '')} bán chạy {year}",
                     f"TikTok {CAT_EN.get(cat, '')} trends {date.today():%B %Y}",
                     f"cách làm video TikTok affiliate {CAT_VI.get(cat, '')} viral {year}"]
        short_kw = " ".join(kw.split()[:2])  # 'Serum dưỡng' finds far more than the full product name
        video_q = [f"review {short_kw} tiktok", f"{short_kw} tiktok shop",
                   f"review {CAT_VI.get(cat, '').split()[0]} tiktok", f"{CAT_EN.get(cat, '')} tiktok viral"]

        articles = self.search_articles(article_q)
        self.fetch_pages(articles)
        videos = self.search_videos(video_q)
        refs = self.reference_links(ref_links or [])

        # Keep the LLM input small: user references + 10 best videos + 10 articles (one per site).
        per_site, slim_articles = set(), []
        for a in articles:
            site = re.sub(r"^https?://(www\.)?", "", a["url"]).split("/")[0]
            if site not in per_site:
                per_site.add(site)
                slim_articles.append(a)
        sources = []
        for item in refs + videos[:10] + slim_articles[:10]:
            item["id"] = len(sources) + 1
            sources.append(item)

        report, source_name, warnings = None, "none", []
        if sources:
            for p in providers.llm_chain():
                try:
                    report = p.summarize_trends(analysis, info, sources)
                    source_name = p.name
                    break
                except providers.ProviderError as e:
                    warnings.append(f"{p.name}: {e}")
        if report is None:
            report = basic_report(sources)
        report["hashtags"] = _merge_hashtags(report.get("hashtags") or [], sources)
        return {"date": date.today().isoformat(), "category": cat, "keyword": kw,
                "report": report, "sources": sources, "summarizer": source_name,
                "warnings": warnings + self.errors}


def _merge_hashtags(from_report: list[str], sources: list[dict], limit: int = 10) -> list[str]:
    counts = Counter()
    for s in sources:
        for tag in re.findall(r"#[\wÀ-ỹ]+", f"{s.get('title', '')} {s.get('snippet', '')}"):
            counts[tag.lower()] += 2 if s["type"] in ("video", "reference") else 1
    tags = [t if t.startswith("#") else f"#{t}" for t in from_report]
    for t, _ in counts.most_common(30):
        if t not in tags and len(t) > 2:
            tags.append(t)
    return tags[:limit]


def basic_report(sources: list[dict]) -> dict:
    """No LLM available: list the raw findings so the user still sees what is hot."""
    vids = [s for s in sources if s["type"] in ("video", "reference")]
    return {
        "summary": "Tổng hợp thô (không có AI tóm tắt): danh sách video và bài viết tìm được.",
        "trending_formats": [], "hook_patterns": [], "content_angles": [], "avoid": [],
        "pacing": "", "music_notes": "", "recommendations": [],
        "top_videos": [{"title": v.get("title", ""), "url": v["url"], "views": v.get("views"),
                        "takeaway": "", "source_id": v["id"]} for v in vids[:8]],
        "hashtags": [],
        "data_quality": "thấp" if len(sources) < 5 else "trung bình",
    }
