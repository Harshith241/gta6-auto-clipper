"""Combine per-second signals, pick peaks, and turn peaks into clip windows."""
import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import find_peaks

from clipper.config import Weights


def _fit(x: np.ndarray | None, n: int) -> np.ndarray:
    out = np.zeros(n)
    if x is not None:
        out[: min(n, len(x))] = x[:n]
    return out


def fuse(chat: np.ndarray | None, audio: np.ndarray | None, clips: np.ndarray | None, w: Weights, n: int) -> np.ndarray:
    score = w.chat * _fit(chat, n) + w.audio * _fit(audio, n) + w.clips * _fit(clips, n)
    return uniform_filter1d(score, size=5, mode="nearest")


def pick_peaks(score: np.ndarray, top: int, min_gap: int, min_score: float) -> list[int]:
    """Top-N peaks, highest first, at least min_gap seconds apart."""
    idx, props = find_peaks(score, height=min_score, distance=max(1, min_gap))
    order = np.argsort(props["peak_heights"])[::-1][:top]
    return [int(idx[i]) for i in order]


def clip_window(peak: int, db: np.ndarray, duration: int, pre: int = 25, post: int = 10,
                min_len: int = 15, max_len: int = 60, snap: int = 3) -> tuple[int, int]:
    """peak-pre .. peak+post, each edge snapped to the quietest second within ±snap, length forced into [min_len, max_len]."""

    def quiet(t: int) -> int:
        lo, hi = max(0, t - snap), min(len(db), t + snap + 1)
        return lo + int(np.argmin(db[lo:hi])) if hi > lo else t

    start, end = quiet(max(0, peak - pre)), quiet(min(duration, peak + post))
    end = min(max(end, start + min_len), start + max_len)
    if end > duration:  # near the end of the VOD: slide back
        start, end = max(0, duration - (end - start)), duration
    return start, end
