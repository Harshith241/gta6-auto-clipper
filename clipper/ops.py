"""Posting ops: campaign rules, post schedule, and the views/earnings tracker."""
import csv
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from clipper.config import Campaign, Config
from clipper.ingest import ClipperError, parse_time

TRACKER = Path("tracker.csv")
TRACK_FIELDS = ["date", "streamer", "campaign", "platform", "category", "clip", "url", "submitted", "views", "earnings"]
SHORTS_TAGS = " #Shorts #GTA6"


def require_campaign(cfg: Config, streamer: str) -> Campaign | None:
    """With campaigns configured, refuse streamers that aren't in any of them."""
    if not cfg.campaigns:
        return None
    c = cfg.campaign_for(streamer)
    if c is None:
        names = ", ".join(sorted({s for c in cfg.campaigns for s in c.streamers}))
        raise ClipperError(f"Streamer '{streamer}' isn't in any campaign in config.yaml (allowed: {names}). "
                           "Pass --streamer if the name was auto-detected wrong.")
    return c


def with_tags(caption: str, tags: list[str]) -> str:
    missing = [t for t in tags if t.lower() not in caption.lower()]
    return (caption + " " + " ".join(missing)).strip() if missing else caption


def platform_text(platform: str, clip: dict[str, Any]) -> tuple[str, str]:
    """(title, caption) for a platform. Shorts: title <=100 chars incl. #Shorts #GTA6."""
    if platform == "youtube":
        return clip.get("title", "")[: 100 - len(SHORTS_TAGS)] + SHORTS_TAGS, clip.get("caption", "")
    return "", clip.get("caption", "")


def load_tracker() -> list[dict[str, str]]:
    if not TRACKER.exists():
        return []
    with TRACKER.open(newline="") as f:
        return list(csv.DictReader(f))


def save_tracker(rows: list[dict[str, Any]]) -> None:
    with TRACKER.open("w", newline="") as f:
        w = csv.DictWriter(f, TRACK_FIELDS)
        w.writeheader()
        w.writerows(rows)


def build_schedule(manifest: dict[str, Any], cfg: Config, accounts: list[str], start: str, gap: str,
                   per_day: int, day: date) -> list[dict[str, Any]]:
    clips = manifest["clips"]
    streamer = manifest["vod"]["streamer"]
    camp = cfg.campaign_for(streamer)
    posted = {(r["clip"], r["platform"]) for r in load_tracker()}  # clip paths stored resolved
    gap_s = parse_time(gap) or 2700
    t0 = datetime.combine(day, datetime.strptime(start, "%H:%M").time())
    rows = []
    for k, platform in enumerate(accounts):
        if camp and platform not in camp.platforms:
            continue  # campaign doesn't pay for this platform
        todo = [c for c in clips if (str(Path(c["file"]).resolve()), platform) not in posted]  # never the same clip twice per platform
        for i, c in enumerate(todo):
            when = t0 + timedelta(days=i // per_day, seconds=(i % per_day) * gap_s + k * gap_s // len(accounts))
            title, caption = platform_text(platform, c)
            rows.append({"post_at": when.strftime("%Y-%m-%d %H:%M"), "platform": platform, "rank": c["rank"],
                         "file": c["file"], "title": title, "caption": caption, "campaign": camp.name if camp else "",
                         "streamer": streamer, "category": c.get("category", "")})
    return sorted(rows, key=lambda r: r["post_at"])


def track_add(clip: Path, platform: str, url: str, cfg: Config, submitted: bool) -> dict[str, Any]:
    rows = load_tracker()
    clip = clip.resolve()
    if any(r["clip"] == str(clip) and r["platform"] == platform for r in rows):
        raise ClipperError(f"{clip.name} is already tracked on {platform}. Never post the same clip twice on one platform.")
    info: dict[str, Any] = {}
    man = clip.parent / "manifest.json"
    if man.exists():
        m = json.loads(man.read_text())
        info = next((c for c in m["clips"] if Path(c["file"]).name == clip.name), {})
        info["streamer"] = m["vod"]["streamer"]
    camp = cfg.campaign_for(info.get("streamer", ""))
    row = {"date": date.today().isoformat(), "streamer": info.get("streamer", ""), "campaign": camp.name if camp else "",
           "platform": platform, "category": info.get("category", ""), "clip": str(clip), "url": url,
           "submitted": "y" if submitted else "n", "views": 0, "earnings": 0.0}
    save_tracker(rows + [row])
    return row


def earnings(views: int, camp: Campaign | None) -> float:
    if camp is None:
        return 0.0
    e = views / 1000 * camp.rate_per_1k
    return round(min(e, camp.cap_per_clip) if camp.cap_per_clip is not None else e, 2)


def track_views(url: str, views: int, cfg: Config) -> dict[str, Any]:
    rows = load_tracker()
    hit = [r for r in rows if r["url"] == url]
    if not hit:
        raise ClipperError(f"No tracked post with url {url}")
    for r in hit:
        r["views"] = views
        r["earnings"] = earnings(views, next((c for c in cfg.campaigns if c.name == r["campaign"]), None))
    save_tracker(rows)
    return hit[0]


def report(rows: list[dict[str, Any]], key: str) -> list[tuple[str, int, int, float]]:
    """(group, posts, views, earnings) sorted by views."""
    agg: dict[str, list[float]] = defaultdict(lambda: [0, 0, 0.0])
    for r in rows:
        a = agg[r[key] or "-"]
        a[0] += 1
        a[1] += int(r["views"] or 0)
        a[2] += float(r["earnings"] or 0)
    return sorted(((k, int(a[0]), int(a[1]), round(a[2], 2)) for k, a in agg.items()), key=lambda x: -x[2])
