"""Whisper transcripts for candidate windows (cached)."""
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import wavfile

from clipper.ingest import console


@lru_cache(maxsize=1)
def _whisper(size: str) -> Any:
    from faster_whisper import WhisperModel

    console.print(f"[cyan]Loading whisper '{size}' (first run downloads the model)...")
    return WhisperModel(size, device="auto", compute_type="int8")


def transcribe(d: Path, start: float, end: float, model_size: str) -> list[dict[str, Any]]:
    """Word timestamps [{w, s, e}] in video seconds for [start, end]. Cached in data/<job>/transcripts/."""
    cache = d / "transcripts" / f"{int(start)}_{int(end)}_{model_size}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    sr, data = wavfile.read(d / "audio.wav", mmap=True)
    audio = np.asarray(data[int(start * sr) : int(end * sr)], dtype=np.float32) / 32768.0
    segments, _ = _whisper(model_size).transcribe(audio, word_timestamps=True, condition_on_previous_text=False)
    words = [{"w": w.word.strip(), "s": round(start + w.start, 2), "e": round(start + w.end, 2)}
             for seg in segments for w in (seg.words or []) if w.word.strip()]
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps(words, ensure_ascii=False))
    return words
