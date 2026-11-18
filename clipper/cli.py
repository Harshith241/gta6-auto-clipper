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


def _analyze(d: Path, top: Optional[int] = None, use_llm: bool = True) -> list[dict]:
    import json

    from rich.progress import Progress

    from clipper.fuse import clip_window, fit_length, fuse, pick_peaks
    from clipper.ingest import load_chat, load_meta
    from clipper.judge import _client, generic_judgment, judge, transcribe
    from clipper.ops import with_tags
    from clipper.signals.audio import audio_signal, loudness_db
    from clipper.signals.chat import chat_signal
    from clipper.signals.twitch_clips import twitch_clips_signal

    cfg = load_config()
    if use_llm:
        _client()  # fail fast on a missing key
    meta = load_meta(d)
    db = loudness_db(d / "audio.wav")
    n = len(db)
    msgs = load_chat(d)
    chat = chat_signal(msgs, n, cfg.keywords, cfg.chat_lag_seconds) if msgs else None
    audio = audio_signal(db)
    clips = twitch_clips_signal(meta, n)
    score = fuse(chat, audio, clips, cfg.weights, n)
    peaks = pick_peaks(score, top or cfg.max_candidates, cfg.min_gap_seconds, cfg.min_score)
    console.print(f"[cyan]{n}s analysed, {len(msgs)} chat msgs, {len(peaks)} peaks")
    camp = cfg.campaign_for(meta["streamer"])
    min_len, max_len = (camp.min_len, camp.max_len) if camp else (15, 60)
    cands = []
    for p in peaks:
        s, e = clip_window(p, db, n, min_len=min_len, max_len=max_len)
        cands.append({
            "peak": p, "start": s, "end": e, "ts": max(0, s - 10), "te": min(n, e + 10),  # ts/te = transcribed range
            "fused": round(float(score[p]), 3),
            "chat": round(float(chat[p]), 3) if chat is not None else 0.0,
            "audio": round(float(audio[p]), 3),
            "clips": round(float(clips[p]), 3) if clips is not None else 0.0,
        })
    fmax = max((c["fused"] for c in cands), default=1.0) or 1.0
    streamer = meta["streamer"]
    kept = []
    with Progress(console=console) as prog:
        task = prog.add_task("Judging candidates" if use_llm else "Ranking", total=len(cands))
        for c in cands:
            prog.advance(task)
            fused_norm = c["fused"] / fmax
            if not use_llm:
                kept.append({**c, **generic_judgment(c, streamer), "llm_score": None, "final": round(fused_norm, 3)})
                continue
            words = transcribe(d, c["ts"], c["te"], cfg.whisper_model)
            in_win = [w for w in words if c["start"] <= w["s"] < c["end"]]
            win_chat = [m for m in msgs if c["start"] <= m["t"] <= c["end"] + cfg.chat_lag_seconds]
            j = judge(c, in_win, win_chat, streamer, cfg.llm_model)
            if j is None or j.score < cfg.min_llm_score:
                continue
            s, e = fit_length(c["start"] + j.start, c["start"] + j.end, n, min_len, max_len)
            s, e = max(s, c["ts"]), min(e, c["te"])  # stay inside the transcribed range
            kept.append({**c, "start": s, "end": e, "llm_score": j.score, "category": j.category, "hook": j.hook,
                         "title": j.title[:60], "caption": j.caption, "reason": j.reason,
                         "final": round(0.6 * j.score / 10 + 0.4 * fused_norm, 3)})
    kept.sort(key=lambda c: c["final"], reverse=True)
    for c in kept:
        c["vod_start"] = meta["offset"] + c["start"]
        if camp:
            c["campaign"] = camp.name
            c["caption"] = with_tags(c["caption"], camp.required_tags)
    if use_llm:
        console.print(f"[cyan]LLM kept {len(kept)}/{len(cands)} (min score {cfg.min_llm_score})")
    (d / "candidates.json").write_text(json.dumps(kept, indent=2, ensure_ascii=False))
    return kept


@app.command()
def analyze(job: str = typer.Argument(..., help="Job id printed by ingest (data/<job>)"),
            top: Optional[int] = typer.Option(None, help="Max candidates"),
            no_llm: bool = typer.Option(False, "--no-llm", help="Skip the Claude judge (signal ranking only)")) -> None:
    """Signals + fusion + judge -> data/<job>/candidates.json (no render)."""
    from rich.table import Table

    from clipper.ingest import job_dir
    from clipper.manifest import hms

    d = job_dir(load_config(), job)
    cands = _analyze(d, top, not no_llm)
    t = Table("#", "VOD time", "len", "final", "llm", "fused", "category", "title", title=f"Candidates -> {d / 'candidates.json'}")
    for i, c in enumerate(cands, 1):
        t.add_row(str(i), hms(c["vod_start"]), f"{c['end'] - c['start']}s", str(c["final"]), str(c.get("llm_score") or "-"),
                  str(c["fused"]), c.get("category", ""), c.get("title", ""))
    console.print(t)


def _render(d: Path, top: int) -> tuple[Path, list[dict]]:
    import json
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from rich.progress import Progress

    from clipper.ingest import load_meta
    from clipper.judge import GENERIC_HOOK, transcribe
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
            words[r["rank"]] = transcribe(d, r.get("ts", r["start"]), r.get("te", r["end"]), cfg.whisper_model)
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
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip the Claude judge (signal ranking only)"),
) -> None:
    """ingest -> analyze -> render, all in one."""
    from clipper.ingest import load_meta
    from clipper.ops import require_campaign

    cfg = load_config()
    if streamer:
        require_campaign(cfg, streamer)  # refuse before downloading gigabytes
    d = _ingest(url, video, chat, streamer, start, end)
    require_campaign(cfg, load_meta(d)["streamer"])
    _analyze(d, None if not no_llm else top, not no_llm)  # LLM mode judges max_candidates, then keeps the best
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


@app.command()
def schedule(job: str = typer.Argument(..., help="Job id with rendered clips"),
             accounts: str = typer.Option("tiktok,youtube,instagram", help="Comma-separated platforms"),
             start: str = typer.Option("18:00", help="First post time HH:MM"),
             gap: str = typer.Option("45m", help="Gap between posts per platform"),
             per_day: int = typer.Option(10, help="Max posts per platform per day"),
             day: Optional[str] = typer.Option(None, help="YYYY-MM-DD (default today)")) -> None:
    """Assign rendered clips to platforms + post times -> schedule.csv."""
    import csv
    import json
    from datetime import date

    from clipper.ingest import job_dir, load_meta
    from clipper.ops import build_schedule

    cfg = load_config()
    meta = load_meta(job_dir(cfg, job))
    out_dir = cfg.out_dir / meta["streamer"] / meta["job"]
    man = out_dir / "manifest.json"
    if not man.exists():
        raise ClipperError(f"No rendered clips for {job}. Run `clipper render {job}` first.")
    rows = build_schedule(json.loads(man.read_text()), cfg, [a.strip() for a in accounts.split(",") if a.strip()],
                          start, gap, per_day, date.fromisoformat(day) if day else date.today())
    path = out_dir / "schedule.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, ["post_at", "platform", "rank", "file", "title", "caption", "campaign", "streamer", "category"])
        w.writeheader()
        w.writerows(rows)
    console.print(f"[green]{len(rows)} posts scheduled -> {path}")


track_app = typer.Typer(no_args_is_help=True, help="Posted-clip tracker (tracker.csv).")
app.add_typer(track_app, name="track")


@track_app.command("add")
def track_add_cmd(clip: Path, platform: str = typer.Option(..., help="tiktok|youtube|instagram|x"),
                  url: str = typer.Option(..., help="Post URL"),
                  submitted: bool = typer.Option(True, help="Link already submitted to the campaign?")) -> None:
    """Log a posted clip."""
    from clipper.ops import track_add

    row = track_add(clip, platform, url, load_config(), submitted)
    console.print(f"[green]Tracked {clip.name} on {platform} ({row['campaign'] or 'no campaign'})")


@track_app.command("views")
def track_views_cmd(url: str, views: int) -> None:
    """Update a post's view count (recomputes estimated earnings)."""
    from clipper.ops import track_views

    row = track_views(url, views, load_config())
    console.print(f"[green]{row['clip']}: {views} views -> ${row['earnings']}")


@track_app.command("report")
def track_report_cmd() -> None:
    """Views + estimated earnings per streamer / category / platform."""
    from rich.table import Table

    from clipper.ops import load_tracker, report

    rows = load_tracker()
    if not rows:
        raise ClipperError("tracker.csv is empty. Log posts with `clipper track add`.")
    for key in ("streamer", "category", "platform"):
        t = Table(key, "posts", "views", "est. $", title=f"By {key}")
        for k, n, v, e in report(rows, key):
            t.add_row(k, str(n), f"{v:,}", f"{e:.2f}")
        console.print(t)
    unsub = [r["url"] for r in rows if r["submitted"] != "y"]
    if unsub:
        console.print(f"[yellow]{len(unsub)} post(s) not submitted to a campaign yet: " + ", ".join(unsub))


@app.command()
def live(channel: str) -> None:
    """[Phase 2, not built yet] Record live in 5-min segments + IRC chat, clip within minutes. See README."""
    raise ClipperError(f"`clipper live {channel}` is planned (streamlink segments + anonymous IRC chat). "
                       "For now run `clipper run <vod_url>` once the VOD is up.")


def main() -> None:
    try:
        app()
    except ClipperError as e:
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)
