"""Download VOD + chat (cached under data/<job_id>/) and extract 16 kHz mono audio."""
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from rich.console import Console

from clipper.config import Config

console = Console()


class ClipperError(Exception):
    """User-facing error: printed without a traceback."""


def require_ffmpeg() -> None:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise ClipperError("ffmpeg/ffprobe not found on PATH. Install it: `brew install ffmpeg` (macOS) or `sudo apt install ffmpeg`.")


def parse_time(s: str | None) -> int | None:
    """'2h', '1h30m', '90m', '45s', '00:12:30', '750' -> seconds."""
    if s is None:
        return None
    s = s.strip()
    if s.isdigit():
        return int(s)
    if ":" in s:
        return int(sum(float(p) * 60**i for i, p in enumerate(reversed(s.split(":")))))
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", s)
    if not m or not any(m.groups()):
        raise ClipperError(f"Bad time '{s}'. Use 2h, 1h30m, 90m, 45s or 00:12:30.")
    h, mi, se = (int(g or 0) for g in m.groups())
    return h * 3600 + mi * 60 + se


def run(cmd: list[str], cwd: Path | None = None) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if p.returncode != 0:
        raise ClipperError(f"{cmd[0]} failed:\n{p.stderr[-1500:]}")


def probe(video: Path) -> tuple[int, int, float]:
    """(width, height, duration_seconds)"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
         "-of", "json", str(video)], capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    return j["streams"][0]["width"], j["streams"][0]["height"], float(j["format"]["duration"])


def extract_audio(video: Path, wav: Path) -> None:
    if wav.exists():
        return
    tmp = wav.with_suffix(".tmp.wav")
    console.print("[cyan]Extracting 16 kHz mono audio...")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(tmp)])
    tmp.rename(wav)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    tmp.rename(path)


def fetch_chat(url: str, path: Path, start: int | None, end: int | None) -> None:
    """Best-effort chat replay. Times stored relative to the downloaded video (i.e. minus `start`)."""
    if path.exists():
        return
    offset = start or 0
    try:
        from chat_downloader import ChatDownloader

        rows = []
        with console.status("[cyan]Downloading chat replay...") as status:
            # max_attempts=1: its retry path blocks on stdin, which breaks non-interactive runs
            for m in ChatDownloader().get_chat(url, start_time=start, end_time=end, max_attempts=1):
                t = m.get("time_in_seconds")
                if t is None:
                    continue
                rows.append({"t": t - offset, "author": (m.get("author") or {}).get("name", ""), "text": m.get("message") or ""})
                if len(rows) % 1000 == 0:
                    status.update(f"[cyan]Downloading chat replay... {len(rows)} msgs (at {int(t)}s)")
        _write_jsonl(path, rows)
        console.print(f"[green]Chat: {len(rows)} messages")
    except Exception as e:  # chat is optional: Kick and some VODs have no replay
        console.print(f"[yellow]Warning: chat unavailable ({type(e).__name__}: {e}). Running audio-only.\n"
                      "  Tip: export chat with TwitchDownloaderCLI and pass --chat chat.json.")


def import_chat(src: Path, dest: Path, offset: int = 0) -> None:
    """Accepts TwitchDownloader JSON ({comments: [...]}), chat-downloader JSON (list), or our jsonl (t/author/text)."""
    raw = src.read_text().strip()
    if raw.startswith("{") and '"comments"' in raw[:5000]:
        items = [{"t": c["content_offset_seconds"], "author": (c.get("commenter") or {}).get("display_name", ""),
                  "text": (c.get("message") or {}).get("body", "")} for c in json.loads(raw)["comments"]]
    elif raw.startswith("["):
        items = [{"t": r["time_in_seconds"], "author": (r.get("author") or {}).get("name", ""), "text": r.get("message") or ""}
                 for r in json.loads(raw) if r.get("time_in_seconds") is not None]
    else:
        items = [json.loads(line) for line in raw.splitlines() if line.strip()]
    rows = [{**r, "t": r["t"] - offset} for r in items if r["t"] >= offset]
    _write_jsonl(dest, rows)
    console.print(f"[green]Chat: imported {len(rows)} messages from {src}")


def ingest_url(url: str, cfg: Config, streamer: str | None = None, start: int | None = None, end: int | None = None,
               chat: Path | None = None) -> Path:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError, download_range_func

    try:
        info = YoutubeDL({"quiet": True, "no_warnings": True}).extract_info(url, download=False)
    except DownloadError as e:
        raise ClipperError(f"Can't open VOD {url} — not found, deleted, or subscriber-only.\n{e}") from e
    vod_id = str(info["id"]).lstrip("v")
    job = vod_id + (f"_{start or 0}-{end or 'end'}" if start or end else "")
    d = cfg.data_dir / job
    d.mkdir(parents=True, exist_ok=True)
    video = d / "video.mp4"
    platform = "kick" if "kick.com" in url else "twitch"
    name = streamer or info.get("uploader_id") or info.get("uploader") or info.get("channel") or "unknown"
    meta = {"id": vod_id, "job": job, "url": url, "platform": platform, "streamer": str(name).lower(),
            "title": info.get("title", ""), "offset": start or 0, "video": str(video)}
    (d / "meta.json").write_text(json.dumps(meta, indent=2))

    if not video.exists():
        console.print(f"[cyan]Downloading VOD {vod_id} ({info.get('title', '')})...")
        opts: dict[str, Any] = {
            "format": "b[height<=1080]/bv*[height<=1080]+ba/b",
            "outtmpl": str(d / "video.%(ext)s"),
            "merge_output_format": "mp4",
            "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
            "quiet": True, "noprogress": False,
        }
        if start or end:
            opts["download_ranges"] = download_range_func(None, [(start or 0, end or info.get("duration") or 10**9)])
            opts["force_keyframes_at_cuts"] = True
        try:
            YoutubeDL(opts).download([url])
        except DownloadError as e:
            raise ClipperError(f"VOD download failed (sub-only or region-locked?): {e}") from e
        if not video.exists():
            raise ClipperError(f"Download finished but {video} is missing; check {d}.")
    else:
        console.print(f"[green]Cached video: {video}")

    extract_audio(video, d / "audio.wav")
    if chat:  # full-VOD chat export; trimmed to the --start/--end window
        import_chat(chat, d / "chat.jsonl", start or 0)
    else:
        fetch_chat(url, d / "chat.jsonl", start, end)
    return d


def ingest_local(video: Path, chat: Path | None, cfg: Config, streamer: str | None = None) -> Path:
    """Local file + optional chat (chat-downloader JSON list, or our jsonl with t/author/text)."""
    if not video.exists():
        raise ClipperError(f"Video not found: {video}")
    d = cfg.data_dir / video.stem
    d.mkdir(parents=True, exist_ok=True)
    meta = {"id": video.stem, "job": video.stem, "url": "", "platform": "local", "streamer": (streamer or "unknown").lower(),
            "title": video.stem, "offset": 0, "video": str(video.resolve())}
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    extract_audio(video, d / "audio.wav")
    if chat:
        import_chat(chat, d / "chat.jsonl")
    return d


def job_dir(cfg: Config, job: str) -> Path:
    d = cfg.data_dir / job
    if not (d / "meta.json").exists():
        raise ClipperError(f"No ingested VOD '{job}' in {cfg.data_dir}/. Run `clipper ingest <url>` first.")
    return d


def load_meta(d: Path) -> dict[str, Any]:
    return json.loads((d / "meta.json").read_text())


def load_chat(d: Path) -> list[dict[str, Any]]:
    p = d / "chat.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []
