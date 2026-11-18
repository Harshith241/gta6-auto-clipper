"""Whisper transcripts for candidate windows (cached) + Claude rating/hook/title."""
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, ValidationError
from scipy.io import wavfile

from clipper.ingest import ClipperError, console

GENERIC_HOOK = "WAIT FOR IT..."


class Judgment(BaseModel):
    score: int = Field(ge=1, le=10)
    category: Literal["funny", "fail", "clutch", "reaction", "story", "bug", "other"]
    start: float
    end: float
    hook: str
    title: str
    caption: str
    reason: str = ""


PROMPT = """You pick viral GTA 6 Shorts/TikToks from {streamer}'s stream.
Below is a candidate moment: the streamer's speech (seconds from window start), chat messages, and hype signals.

Signals: fused={fused} chat={chat} audio={audio}
Window length: {length}s

TRANSCRIPT:
{transcript}

CHAT:
{chat_lines}

Rate how well this works as a standalone 15-60s short for people who don't know the stream.
Pick the best start/end (seconds from window start) so it starts right before the setup and ends on the payoff.
Reply with ONLY this JSON object, no prose:
{{"score": 1-10, "category": "funny|fail|clutch|reaction|story|bug|other", "start": sec, "end": sec,
"hook": "<=6 words, on-screen text for the first 3s", "title": "<=60 chars", "caption": "post caption with 3-5 hashtags incl #GTA6", "reason": "one line"}}"""


def parse_judgment(text: str) -> Judgment:
    """Pull the first {...} out of the reply and validate. Raises ValueError on garbage."""
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("no JSON object in reply")
    try:
        return Judgment.model_validate_json(text[i : j + 1])
    except ValidationError as e:
        raise ValueError(str(e)) from e


def generic_judgment(cand: dict[str, Any], streamer: str) -> dict[str, Any]:
    """--no-llm fallback: signal ranking + generic hook/title/caption."""
    return {"category": "other", "hook": GENERIC_HOOK, "title": f"{streamer} GTA 6 moment",
            "caption": f"{streamer} in GTA 6 😂 #GTA6 #GTA6Online #{streamer} #gaming #fyp"}


@lru_cache(maxsize=1)
def _client() -> Any:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ClipperError("ANTHROPIC_API_KEY is not set. Add it to .env, or rerun with --no-llm to skip the LLM judge.")
    import anthropic

    return anthropic.Anthropic()


def judge(cand: dict[str, Any], words: list[dict[str, Any]], chat: list[dict[str, Any]], streamer: str,
          model: str) -> Judgment | None:
    """One Claude call (+1 retry on invalid JSON). Returns None if it can't get a valid answer."""
    import anthropic

    s0 = cand["start"]
    transcript = "\n".join(f"[{w['s'] - s0:.1f}] {w['w']}" for w in words) or "(no speech)"
    chat_lines = "\n".join(f"[{m['t'] - s0:.0f}] {m['author']}: {m['text'][:120]}" for m in chat[:150]) or "(no chat)"
    prompt = PROMPT.format(streamer=streamer, fused=cand["fused"], chat=cand["chat"], audio=cand["audio"],
                           length=cand["end"] - s0, transcript=transcript, chat_lines=chat_lines)
    err = ""
    for _ in range(2):
        try:
            resp = _client().messages.create(
                model=model, max_tokens=4000, output_config={"effort": "low"},
                messages=[{"role": "user", "content": prompt + (f"\n\nYour previous reply was invalid ({err}). Reply with only the JSON object." if err else "")}])
        except anthropic.AuthenticationError as e:
            raise ClipperError("Anthropic API key rejected. Fix ANTHROPIC_API_KEY in .env or use --no-llm.") from e
        except anthropic.APIError as e:
            console.print(f"[yellow]LLM call failed for candidate at {s0}s: {e}")
            return None
        if resp.stop_reason == "refusal":
            return None
        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            return parse_judgment(text)
        except ValueError as e:
            err = str(e)[:300]
    console.print(f"[yellow]Dropped candidate at {s0}s: model returned invalid JSON twice.")
    return None


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
