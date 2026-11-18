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


def main() -> None:
    try:
        app()
    except ClipperError as e:
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)
