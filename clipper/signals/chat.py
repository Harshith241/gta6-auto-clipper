"""Chat hype per second: rate z-score x unique-chatter ratio + keyword/emote hits, shifted for chat lag."""
import re
from typing import Any

import numpy as np

from clipper.signals import rolling_z


def _matchers(keywords: dict[str, float]) -> list[tuple[re.Pattern[str], float]]:
    # word-ish keys match whole words ("W" must not match "WOW"); symbols/emoji match as substrings
    return [(re.compile(rf"(?<!\w){re.escape(k)}(?!\w)" if re.search(r"\w", k) else re.escape(k)), w)
            for k, w in keywords.items()]


def chat_signal(msgs: list[dict[str, Any]], duration: int, keywords: dict[str, float], lag: int = 7) -> np.ndarray:
    rate = np.zeros(duration)
    kw = np.zeros(duration)
    authors: list[set[str]] = [set() for _ in range(duration)]
    matchers = _matchers(keywords)
    for m in msgs:
        i = int(m["t"])
        if not 0 <= i < duration:
            continue
        rate[i] += 1
        authors[i].add(m.get("author", ""))
        text = m.get("text", "")
        kw[i] += sum(w for pat, w in matchers if pat.search(text))
    uniq = np.array([len(a) for a in authors], dtype=float)
    uniq_ratio = np.divide(uniq, rate, out=np.zeros(duration), where=rate > 0)  # 1 spammer -> low ratio
    z = np.clip(rolling_z(rate), 0, 6) * (0.3 + 0.7 * uniq_ratio)
    score = z + np.log1p(np.clip(rolling_z(kw), 0, None))
    # chat reacts ~lag s after the moment: value at t moves to t - lag
    shifted = np.zeros(duration)
    if lag < duration:
        shifted[: duration - lag] = score[lag:]
    return shifted
