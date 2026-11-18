"""Optional bonus: viewer-made Twitch clips of this VOD (Helix API). Needs TWITCH_CLIENT_ID/SECRET; else skipped."""
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from scipy.ndimage import gaussian_filter1d

from clipper.ingest import console, parse_time


def _get(url: str, headers: dict[str, str], data: bytes | None = None) -> Any:
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=20) as r:
        return json.loads(r.read())


def fetch_clips(vod_id: str) -> list[dict[str, Any]]:
    cid, secret = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
    if not (cid and secret):
        return []
    body = urllib.parse.urlencode({"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"}).encode()
    token = _get("https://id.twitch.tv/oauth2/token", {}, body)["access_token"]
    h = {"Client-Id": cid, "Authorization": f"Bearer {token}"}
    v = _get(f"https://api.twitch.tv/helix/videos?id={vod_id}", h)["data"][0]
    t0 = datetime.fromisoformat(v["created_at"].replace("Z", "+00:00"))
    t1 = t0 + timedelta(seconds=parse_time(v["duration"]) or 0)
    q = {"broadcaster_id": v["user_id"], "started_at": t0.isoformat().replace("+00:00", "Z"),
         "ended_at": t1.isoformat().replace("+00:00", "Z"), "first": 100}
    clips: list[dict[str, Any]] = []
    for _ in range(5):  # ponytail: 500 clips max; top clips are on the first pages anyway
        page = _get("https://api.twitch.tv/helix/clips?" + urllib.parse.urlencode(q), h)
        clips += [c for c in page["data"] if c.get("video_id") == vod_id and c.get("vod_offset") is not None]
        if not (cursor := page.get("pagination", {}).get("cursor")):
            break
        q["after"] = cursor
    return clips


def clips_signal(clips: list[dict[str, Any]], offset: int, n: int) -> np.ndarray:
    """Bump near the end of each clip (where the payoff usually is), weighted by log views."""
    sig = np.zeros(n)
    for c in clips:
        t = int(c["vod_offset"] + 0.7 * c.get("duration", 30) - offset)
        if 0 <= t < n:
            sig[t] += np.log1p(c.get("view_count", 0))
    sigma = 4
    return gaussian_filter1d(sig, sigma) * np.sqrt(2 * np.pi) * sigma  # keep peak height ~ log views


def twitch_clips_signal(meta: dict[str, Any], n: int) -> np.ndarray | None:
    if meta["platform"] != "twitch" or not os.environ.get("TWITCH_CLIENT_ID"):
        return None
    try:
        clips = fetch_clips(meta["id"])
    except Exception as e:
        console.print(f"[yellow]Twitch clips signal skipped ({type(e).__name__}: {e})")
        return None
    console.print(f"[cyan]Twitch clips: {len(clips)} viewer clips for this VOD")
    return clips_signal(clips, meta["offset"], n) if clips else None
