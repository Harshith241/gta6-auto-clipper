"""clipper CLI: ingest, analyze, render, run, calibrate."""
from pathlib import Path
from typing import Optional

import typer

from clipper.config import load_config
from clipper.ingest import ClipperError, console, ingest_local, ingest_url, parse_time, require_ffmpeg

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False, help="GTA 6 VOD -> 9:16 Shorts.")


@app.callback()
def _startup() -> None:
    require_ffmpeg()


def _ingest(url: Optional[str], video: Optional[Path], chat: Optional[Path], streamer: Optional[str],
            start: Optional[str], end: Optional[str]) -> Path:
    cfg = load_config()
    if video:
        return ingest_local(video, chat, cfg, streamer)
    if not url:
        raise ClipperError("Pass a VOD URL or --video local.mp4 [--chat chat.json].")
    return ingest_url(url, cfg, streamer, parse_time(start), parse_time(end), chat)


@app.command()
def ingest(
    url: Optional[str] = typer.Argument(None, help="Twitch/Kick VOD URL"),
    video: Optional[Path] = typer.Option(None, help="Local video instead of a URL"),
    chat: Optional[Path] = typer.Option(None, help="Local chat JSON/JSONL for --video"),
    streamer: Optional[str] = typer.Option(None),
    start: Optional[str] = typer.Option(None, help="e.g. 2h, 1h30m, 00:12:30"),
    end: Optional[str] = typer.Option(None),
) -> None:
    """Download VOD + chat and extract audio (cached)."""
    d = _ingest(url, video, chat, streamer, start, end)
    console.print(f"[bold green]Ingested -> {d}  (job id: {d.name})")


def _analyze(d: Path, top: Optional[int] = None) -> list[dict]:
    import json

    import numpy as np

    from clipper.fuse import clip_window, fuse, pick_peaks
    from clipper.ingest import load_chat, load_meta
    from clipper.signals.audio import audio_signal, loudness_db
    from clipper.signals.chat import chat_signal

    cfg = load_config()
    meta = load_meta(d)
    db = loudness_db(d / "audio.wav")
    n = len(db)
    msgs = load_chat(d)
    chat = chat_signal(msgs, n, cfg.keywords, cfg.chat_lag_seconds) if msgs else None
    audio = audio_signal(db)
    score = fuse(chat, audio, None, cfg.weights, n)
    peaks = pick_peaks(score, top or cfg.max_candidates, cfg.min_gap_seconds, cfg.min_score)
    console.print(f"[cyan]{n}s analysed, {len(msgs)} chat msgs, {len(peaks)} peaks")
    cands = []
    for p in peaks:
        s, e = clip_window(p, db, n)
        cands.append({
            "peak": p, "start": s, "end": e, "vod_start": meta["offset"] + s,
            "fused": round(float(score[p]), 3),
            "chat": round(float(chat[p]), 3) if chat is not None else 0.0,
            "audio": round(float(audio[p]), 3),
        })
    fmax = max((c["fused"] for c in cands), default=1.0) or 1.0
    for c in cands:
        c["final"] = round(c["fused"] / fmax, 3)
    (d / "candidates.json").write_text(json.dumps(cands, indent=2))
    return cands


def _hms(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


@app.command()
def analyze(job: str = typer.Argument(..., help="Job id printed by ingest (data/<job>)"),
            top: Optional[int] = typer.Option(None, help="Max candidates")) -> None:
    """Signals + fusion -> data/<job>/candidates.json (no render)."""
    from rich.table import Table

    from clipper.ingest import job_dir

    d = job_dir(load_config(), job)
    cands = _analyze(d, top)
    t = Table("#", "VOD time", "len", "fused", "chat", "audio", title=f"Candidates -> {d / 'candidates.json'}")
    for i, c in enumerate(cands, 1):
        t.add_row(str(i), _hms(c["vod_start"]), f"{c['end'] - c['start']}s", str(c["fused"]), str(c["chat"]), str(c["audio"]))
    console.print(t)


def main() -> None:
    try:
        app()
    except ClipperError as e:
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)
