"""Audio hype per second: loudness z-score + sudden-onset bonus, clipped so one gunfight can't dominate."""
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from clipper.signals import rolling_z


def loudness_db(wav: Path) -> np.ndarray:
    """RMS dB per 1s bucket. Chunked + mmap so multi-hour VODs don't load into RAM."""
    sr, data = wavfile.read(wav, mmap=True)
    n = len(data) // sr
    out = np.empty(n)
    step = 600  # seconds per chunk
    for s in range(0, n, step):
        e = min(n, s + step)
        x = np.asarray(data[s * sr : e * sr], dtype=np.float32).reshape(e - s, sr) / 32768.0
        out[s:e] = 20 * np.log10(np.sqrt((x * x).mean(axis=1)) + 1e-6)
    return out


def audio_signal(db: np.ndarray) -> np.ndarray:
    z = np.clip(rolling_z(db, min_std=3.0), 0, 4)
    # onset: jump above the mean of the previous 5 s (quiet -> scream/laugh)
    prev = np.convolve(np.concatenate([np.full(5, db[0] if len(db) else 0), db]), np.ones(5) / 5, "valid")[:-1]
    onset = np.clip((db - prev - 6) / 6, 0, 2)
    return z + onset
