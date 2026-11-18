"""clipper CLI: ingest, analyze, render, run, calibrate."""
from pathlib import Path
from typing import Optional

import typer

from clipper.config import load_config
from clipper.ingest import ClipperError, console, ingest_local, ingest_url, parse_time, require_ffmpeg

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False, help="GTA 6 VOD -> 9:16 Shorts.")
GENERIC_HOOK = "WAIT FOR IT..."


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


@app.command()
def analyze(job: str = typer.Argument(..., help="Job id printed by ingest (data/<job>)"),
            top: Optional[int] = typer.Option(None, help="Max candidates")) -> None:
    """Signals + fusion -> data/<job>/candidates.json (no render)."""
    from rich.table import Table

    from clipper.ingest import job_dir
    from clipper.manifest import hms

    d = job_dir(load_config(), job)
    cands = _analyze(d, top)
    t = Table("#", "VOD time", "len", "fused", "chat", "audio", title=f"Candidates -> {d / 'candidates.json'}")
    for i, c in enumerate(cands, 1):
        t.add_row(str(i), hms(c["vod_start"]), f"{c['end'] - c['start']}s", str(c["fused"]), str(c["chat"]), str(c["audio"]))
    console.print(t)


def _render(d: Path, top: int) -> tuple[Path, list[dict]]:
    import json
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from rich.progress import Progress

    from clipper.ingest import load_meta
    from clipper.judge import transcribe
    from clipper.manifest import hms, slug, vod_link, write_manifest
    from clipper.render import render_clip

    cfg = load_config()
    meta = load_meta(d)
    cpath = d / "candidates.json"
    if not cpath.exists():
        raise ClipperError(f"No candidates for {d.name}. Run `clipper analyze {d.name}` first.")
    cands = json.loads(cpath.read_text())[:top]
    out_dir = cfg.out_dir / meta["streamer"] / meta["job"]
    facecam = cfg.streamer(meta["streamer"]).facecam
    rows = []
    for i, c in enumerate(cands, 1):
        f = out_dir / f"clip_{i:02d}_{slug(c.get('title') or c.get('category') or 'moment')}.mp4"
        rows.append({**c, "rank": i, "file": str(f), "vod_time": hms(c["vod_start"]), "vod_link": vod_link(meta, c["vod_start"])})
    todo = [r for r in rows if not Path(r["file"]).exists()]
    words = {}
    with Progress(console=console) as prog:  # whisper is CPU-bound: transcribe sequentially, render in parallel
        task = prog.add_task("Transcribing captions", total=len(todo))
        for r in todo:
            words[r["rank"]] = transcribe(d, r["start"], r["end"], cfg.whisper_model)
            prog.advance(task)
    with Progress(console=console) as prog, ThreadPoolExecutor(cfg.render_workers) as pool:
        task = prog.add_task(f"Rendering {len(rows)} clips", total=len(rows))
        futs = {pool.submit(render_clip, Path(meta["video"]), r["start"], r["end"], Path(r["file"]), facecam,
                            words[r["rank"]], r.get("hook") or GENERIC_HOOK): r for r in todo}
        prog.advance(task, len(rows) - len(futs))  # already rendered
        for fut in as_completed(futs):
            fut.result()
            prog.advance(task)
    write_manifest(out_dir, meta, rows)
    return out_dir, rows


def _summary(out_dir: Path, rows: list[dict]) -> None:
    from rich.table import Table

    t = Table("#", "VOD time", "score", "category", "title / hook", "file", title=f"{len(rows)} clips -> {out_dir}")
    for r in rows:
        t.add_row(str(r["rank"]), r["vod_time"], str(r.get("final", "")), r.get("category", ""),
                  r.get("title") or r.get("hook") or "", Path(r["file"]).name)
    console.print(t)
    console.print(f"Manifest: {out_dir / 'manifest.json'}  CSV: {out_dir / 'clips.csv'}")


@app.command()
def render(job: str = typer.Argument(..., help="Job id (data/<job>)"), top: int = typer.Option(10, help="Render the best N")) -> None:
    """Render top candidates to 9:16 MP4s + manifest."""
    from clipper.ingest import job_dir

    out_dir, rows = _render(job_dir(load_config(), job), top)
    _summary(out_dir, rows)


@app.command("run")
def run_all(
    url: Optional[str] = typer.Argument(None, help="Twitch/Kick VOD URL"),
    video: Optional[Path] = typer.Option(None, help="Local video instead of a URL"),
    chat: Optional[Path] = typer.Option(None, help="Chat JSON/JSONL export"),
    streamer: Optional[str] = typer.Option(None),
    start: Optional[str] = typer.Option(None, help="e.g. 1h"),
    end: Optional[str] = typer.Option(None, help="e.g. 3h"),
    top: int = typer.Option(10, help="Clips to render"),
) -> None:
    """ingest -> analyze -> render, all in one."""
    d = _ingest(url, video, chat, streamer, start, end)
    _analyze(d, max(top * 2, top))  # over-fetch; later filters drop some
    out_dir, rows = _render(d, top)
    _summary(out_dir, rows)


@app.command()
def calibrate(job: str = typer.Argument(..., help="Job id (data/<job>)"),
              at: str = typer.Option("0:05:00", help="VOD timestamp, e.g. 00:12:30")) -> None:
    """Export one frame as PNG so you can read off the facecam box (x, y, w, h)."""
    from clipper.ingest import job_dir, load_meta, parse_time, probe
    from clipper.render import export_frame

    d = job_dir(load_config(), job)
    meta = load_meta(d)
    video = Path(meta["video"])
    w, h, dur = probe(video)
    t = min(max(0, (parse_time(at) or 0) - meta["offset"]), dur - 1)
    png = export_frame(video, t, d / f"calibrate_{int(t)}.png")
    console.print(f"[green]Frame -> {png.resolve()}  (source {w}x{h})")
    console.print("Open it, find the facecam rectangle in source pixels, then add to config.yaml:\n"
                  f"streamers:\n  {meta['streamer']}:\n    facecam: {{ x: 1440, y: 0, w: 480, h: 270 }}")


def main() -> None:
    try:
        app()
    except ClipperError as e:
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)
