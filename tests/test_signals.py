import numpy as np
from scipy.io import wavfile

from clipper.config import DEFAULT_KEYWORDS, Weights
from clipper.fuse import clip_window, fuse, pick_peaks
from clipper.signals.audio import audio_signal, loudness_db
from clipper.signals.chat import chat_signal

rng = np.random.default_rng(0)


def test_chat_spike_detected_with_lag() -> None:
    msgs = [{"t": t + rng.random(), "author": f"u{rng.integers(500)}", "text": "hi"} for t in range(600) for _ in range(2)]
    msgs += [{"t": 300 + rng.random() * 3, "author": f"v{i}", "text": "KEKW CLIP IT"} for i in range(80)]
    sig = chat_signal(msgs, 600, DEFAULT_KEYWORDS, lag=7)
    assert abs(int(np.argmax(sig)) - (300 - 7)) <= 3


def test_single_spammer_scores_lower_than_crowd() -> None:
    base = [{"t": t, "author": f"u{t}", "text": "hi"} for t in range(600)]
    spam = base + [{"t": 300, "author": "bot", "text": "x"} for _ in range(60)]
    crowd = base + [{"t": 300, "author": f"v{i}", "text": "x"} for i in range(60)]
    assert chat_signal(spam, 600, {}, 0).max() < chat_signal(crowd, 600, {}, 0).max()


def test_audio_burst_detected(tmp_path) -> None:
    sr = 16000
    x = rng.normal(0, 0.01, sr * 300)
    x[120 * sr : 123 * sr] = rng.normal(0, 0.5, 3 * sr)
    wavfile.write(tmp_path / "a.wav", sr, (x * 32767).clip(-32768, 32767).astype(np.int16))
    sig = audio_signal(loudness_db(tmp_path / "a.wav"))
    assert 119 <= int(np.argmax(sig)) <= 123


def test_fusion_respects_min_gap_and_top_n() -> None:
    s = np.zeros(1000)
    for p, h in [(100, 5), (130, 4), (400, 3), (700, 6), (900, 2)]:
        s[p] = h
    score = fuse(s, None, None, Weights(chat=1, audio=0, clips=0), 1000)
    peaks = pick_peaks(score, top=3, min_gap=60, min_score=0.1)
    assert len(peaks) == 3
    assert peaks[0] == 700
    assert all(abs(a - b) >= 60 for a in peaks for b in peaks if a != b)
    assert 130 not in peaks  # suppressed by the bigger peak at 100


def test_window_length_always_15_to_60() -> None:
    for _ in range(300):
        n = int(rng.integers(120, 5000))
        db = rng.normal(-30, 10, n)
        peak = int(rng.integers(0, n))
        pre, post = int(rng.integers(0, 80)), int(rng.integers(0, 80))
        s, e = clip_window(peak, db, n, pre=pre, post=post)
        assert 15 <= e - s <= 60 and 0 <= s < e <= n
