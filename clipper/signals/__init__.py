import numpy as np
from scipy.ndimage import uniform_filter1d


def rolling_z(x: np.ndarray, window: int = 300, min_std: float = 1.0) -> np.ndarray:
    """z-score of each second against a centered rolling baseline (default 5 min)."""
    x = x.astype(np.float64)
    if len(x) == 0:
        return x
    mean = uniform_filter1d(x, size=window, mode="nearest")
    var = uniform_filter1d(x * x, size=window, mode="nearest") - mean**2
    return (x - mean) / np.maximum(np.sqrt(np.maximum(var, 0)), min_std)
