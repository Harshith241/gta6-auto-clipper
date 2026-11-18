"""manifest.json + clips.csv for rendered clips."""
import csv
import json
import re
from pathlib import Path
from typing import Any

FIELDS = ["rank", "file", "vod_time", "vod_link", "final", "llm_score", "fused", "chat", "audio", "clips",
          "category", "title", "caption", "hook", "start", "end"]


def slug(text: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:n].strip("_") or "clip"


def hms(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def vod_link(meta: dict[str, Any], vod_t: float) -> str:
    t = int(vod_t)
    if meta["platform"] == "twitch":
        return f"https://www.twitch.tv/videos/{meta['id']}?t={t // 3600}h{t % 3600 // 60}m{t % 60}s"
    if meta["platform"] == "kick":
        return f"{meta['url']}?t={t}"
    return ""


def write_manifest(out_dir: Path, meta: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps({"vod": meta, "clips": rows}, indent=2, ensure_ascii=False))
    with (out_dir / "clips.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
