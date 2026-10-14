"""Short-form content planning: hooks, scripts, captions and a posting calendar.

Output is a plan for review, not auto-published content. Publishing must go
through official APIs or a scheduler the owner authorises. Rules baked in:
no fabricated reviews, results, scarcity or sales claims; paid/affiliate
partnerships must be disclosed on-platform.
"""
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from itertools import cycle

FORMATS = {
    "process": {
        "hook": "Watch this {product} get made from a blank",
        "beats": ["Blank item on table", "Design reveal on screen", "Finished item close-up", "Styled in real setting"],
        "needs": "a real sample unit (order one at your own cost before posting)",
    },
    "relatable": {
        "hook": "POV: you're a {audience} and someone asks 'how many is too many?'",
        "beats": ["Text-on-screen joke", "Pan across the real situation", "Reveal design that says it", "Call to action"],
        "needs": "original footage of your own space, no copyrighted audio unless licensed via the platform library",
    },
    "gift": {
        "hook": "A gift for the {audience} who already has everything",
        "beats": ["Gift box opening", "Item reveal", "Detail of print quality", "Where to get it"],
        "needs": "a real sample unit",
    },
    "design_story": {
        "hook": "I designed this for {audience} because {reason}",
        "beats": ["Sketch/early draft", "Iterations", "Final on product mockup", "Ask viewers which variant next"],
        "needs": "your design files; mockups must be labelled as mockups",
    },
}

PLATFORM_RULES = {
    "tiktok": {"max_len_s": 60, "caption_chars": 2200, "tags": 3},
    "instagram_reels": {"max_len_s": 90, "caption_chars": 2200, "tags": 5},
    "youtube_shorts": {"max_len_s": 60, "caption_chars": 100, "tags": 3},
    "pinterest": {"max_len_s": 60, "caption_chars": 500, "tags": 0},
}

BANNED_CLAIMS = ["best seller", "selling out", "only a few left", "#1", "clinically", "guaranteed", "5-star", "everyone loves"]


@dataclass
class ContentItem:
    day: str
    platform: str
    product: str
    format: str
    hook: str
    beats: list
    caption: str
    hashtags: list
    production_needs: str
    status: str = "draft_for_review"


def check_claims(text: str) -> list[str]:
    low = text.lower()
    return [c for c in BANNED_CLAIMS if c in low]


def make_item(day: date, platform: str, product: str, fmt: str, audience: str, tags: list, reason: str = "") -> ContentItem:
    f = FORMATS[fmt]
    rules = PLATFORM_RULES[platform]
    hook = f["hook"].format(product=product.lower(), audience=audience, reason=reason or "it didn't exist yet")
    caption = f"{hook}. Original design, printed to order. Link in bio."[: rules["caption_chars"]]
    problems = check_claims(hook + caption)
    if problems:
        raise ValueError(f"unsupported claims: {problems}")
    return ContentItem(day.isoformat(), platform, product, fmt, hook, f["beats"], caption,
                       tags[: rules["tags"]], f["needs"])


def calendar(products: list[str], audience: str, tags: list, start: date, days: int = 14,
             platforms=("tiktok", "instagram_reels", "youtube_shorts"), per_day: int = 1) -> list[dict]:
    """Rotate products x formats; each concept is cross-posted per platform rules."""
    combos = cycle([(p, f) for f in FORMATS for p in products])
    plan = []
    for d in range(days):
        day = start + timedelta(days=d)
        for _ in range(per_day):
            product, fmt = next(combos)
            for platform in platforms:
                plan.append(asdict(make_item(day, platform, product, fmt, audience, tags)))
    return plan


def record_performance(plan_item: dict, views: int, likes: int, shares: int, clicks: int, source: str) -> dict:
    """Attach REAL platform analytics (source required) for format ranking."""
    if not source:
        raise ValueError("performance needs a data source")
    return dict(plan_item, metrics={"views": views, "likes": likes, "shares": shares, "clicks": clicks, "source": source})


def rank_formats(items: list[dict]) -> list[tuple[str, float]]:
    """Click-through per 1k views by format, from recorded metrics only."""
    agg: dict[str, list[int]] = {}
    for it in items:
        m = it.get("metrics")
        if not m:
            continue
        a = agg.setdefault(it["format"], [0, 0])
        a[0] += m["views"]
        a[1] += m["clicks"]
    return sorted(((f, round(1000 * c / v, 2) if v else 0.0) for f, (v, c) in agg.items()), key=lambda x: -x[1])
