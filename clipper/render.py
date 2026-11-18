"""9:16 render (1080x1920): blurred-background or facecam-top/gameplay-bottom layout, ASS captions + hook."""
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from clipper.config import Box
from clipper.ingest import console, run

W, H = 1080, 1920
TOP_H = 672  # facecam band ~35%
BOT_H = H - TOP_H
FONTS_DIR = "/System/Library/Fonts/Supplemental"  # macOS; ignored by libass if missing


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    """System ffmpeg if it has libass, else the libass-enabled build bundled with imageio-ffmpeg."""
    if has_ass("ffmpeg"):
        return "ffmpeg"
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        console.print("[yellow]Warning: ffmpeg has no libass; captions disabled. Install a full ffmpeg build.")
        return "ffmpeg"


def has_ass(ffmpeg: str) -> bool:
    out = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return bool(re.search(r"\sass\s", out))


def video_filter(facecam: Box | None) -> str:
    """Filter graph ending in [base]."""
    if facecam:
        f = facecam
        return (f"[0:v]split[a][b];"
                f"[a]crop={f.w}:{f.h}:{f.x}:{f.y},scale={W}:{TOP_H}:force_original_aspect_ratio=increase,crop={W}:{TOP_H}[top];"
                f"[b]scale=-2:{BOT_H},crop={W}:{BOT_H}[bot];"
                f"[top][bot]vstack,setsar=1[base]")
    return (f"[0:v]split[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=40[bg];"
            f"[b]scale={W}:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]")


def _ts(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _clean(s: str) -> str:
    return re.sub(r"[{}\\]", "", s).upper()


def ass_script(words: list[dict[str, Any]], start: float, end: float, hook: str, cap_y: int, hook_y: int) -> str:
    """Word-by-word captions (active word yellow) in chunks of <=3 words + hook text for the first 3 s."""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial Black,86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,3,5,60,60,0,1
Style: Hook,Arial Black,80,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,3,18,0,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if hook:
        ev.append(f"Dialogue: 1,{_ts(0)},{_ts(min(3, end - start))},Hook,,0,0,0,,{{\\pos({W // 2},{hook_y})\\fad(0,250)}}{_clean(hook)}")
    ws = [w for w in words if start <= w["s"] < end]
    chunks: list[list[dict[str, Any]]] = []
    for w in ws:  # new chunk every 3 words or after a pause
        if not chunks or len(chunks[-1]) == 3 or w["s"] - chunks[-1][-1]["e"] > 0.6:
            chunks.append([])
        chunks[-1].append(w)
    for ch in chunks:
        for j, w in enumerate(ch):
            t0 = w["s"] - start
            t1 = (ch[j + 1]["s"] if j + 1 < len(ch) else ch[-1]["e"] + 0.15) - start
            text = " ".join((r"{\c&H00FFFF&}" + _clean(x["w"]) + r"{\c&HFFFFFF&}") if k == j else _clean(x["w"])
                            for k, x in enumerate(ch))
            ev.append(f"Dialogue: 0,{_ts(t0)},{_ts(min(t1, end - start))},Cap,,0,0,0,,{{\\pos({W // 2},{cap_y})}}{text}")
    return head + "\n".join(ev) + "\n"


def render_clip(video: Path, start: float, end: float, out: Path, facecam: Box | None = None,
                words: list[dict[str, Any]] | None = None, hook: str = "") -> Path:
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.mp4")
    ff = ffmpeg_bin()
    graph = video_filter(facecam)
    if (words or hook) and has_ass(ff):
        cap_y, hook_y = (TOP_H, TOP_H + 260) if facecam else (1400, 470)
        ass = out.with_suffix(".ass")
        ass.write_text(ass_script(words or [], start, end, hook, cap_y, hook_y), encoding="utf-8")
        # run from the clip's folder so the ass= path needs no escaping (paths with spaces/colons)
        graph += f";[base]ass={ass.name}:fontsdir={FONTS_DIR}[v]"
    else:
        graph += ";[base]null[v]"
    run([ff, "-y", "-v", "error", "-ss", f"{start:.2f}", "-i", str(Path(video).resolve()), "-t", f"{end - start:.2f}",
         "-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
         "-movflags", "+faststart", tmp.name], cwd=out.parent)
    tmp.rename(out)
    return out


def export_frame(video: Path, at: float, png: Path) -> Path:
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", str(png)])
    return png
