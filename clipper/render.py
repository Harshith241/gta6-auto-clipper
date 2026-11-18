"""9:16 render (1080x1920): blurred-background or facecam-top/gameplay-bottom layout."""
from pathlib import Path

from clipper.config import Box
from clipper.ingest import run

W, H = 1080, 1920
TOP_H = 672  # facecam band ~35%
BOT_H = H - TOP_H


def video_filter(facecam: Box | None) -> str:
    if facecam:
        f = facecam
        return (f"[0:v]split[a][b];"
                f"[a]crop={f.w}:{f.h}:{f.x}:{f.y},scale={W}:{TOP_H}:force_original_aspect_ratio=increase,crop={W}:{TOP_H}[top];"
                f"[b]scale=-2:{BOT_H},crop={W}:{BOT_H}[bot];"
                f"[top][bot]vstack,setsar=1[v]")
    return (f"[0:v]split[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=40[bg];"
            f"[b]scale={W}:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")


def render_clip(video: Path, start: float, end: float, out: Path, facecam: Box | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.mp4")
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-i", str(video), "-t", f"{end - start:.2f}",
         "-filter_complex", video_filter(facecam), "-map", "[v]", "-map", "0:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
         "-movflags", "+faststart", str(tmp)])
    tmp.rename(out)
    return out
