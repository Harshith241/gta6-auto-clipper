"""Settings from config.yaml + .env."""
import os
from pathlib import Path

import yaml
from pydantic import BaseModel


class Box(BaseModel):
    x: int
    y: int
    w: int
    h: int


class Streamer(BaseModel):
    platform: str = "twitch"
    facecam: Box | None = None


class Weights(BaseModel):
    chat: float = 1.0
    audio: float = 0.7
    clips: float = 1.5


DEFAULT_KEYWORDS: dict[str, float] = {
    "KEKW": 2, "LUL": 1.5, "OMEGALUL": 2, "LMAO": 1.5, "W": 1, "L": 1, "???": 1.5,
    "NO WAY": 2, "CLIP IT": 3, "CLIP": 2, "WHAT": 1, "HOLY": 1.5, "Pog": 1.5,
    "PogChamp": 1.5, "monkaS": 1.5, "💀": 1.5, "😂": 1.5,
}


class Config(BaseModel):
    streamers: dict[str, Streamer] = {"default": Streamer()}
    weights: Weights = Weights()
    chat_lag_seconds: int = 7
    max_candidates: int = 25
    min_gap_seconds: int = 60
    min_score: float = 1.0  # minimum fused score for a peak
    min_llm_score: int = 6
    whisper_model: str = "small"
    llm_model: str = "claude-haiku-5-5"
    render_workers: int = 3
    keywords: dict[str, float] = DEFAULT_KEYWORDS
    data_dir: Path = Path("data")
    out_dir: Path = Path("out")

    def streamer(self, name: str | None) -> Streamer:
        return self.streamers.get(name or "") or self.streamers.get("default") or Streamer()


def load_env(path: Path = Path(".env")) -> None:
    # ponytail: 5-line .env reader instead of python-dotenv; no quoting/multiline support
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path: Path = Path("config.yaml")) -> Config:
    load_env()
    if not path.exists():
        return Config()
    return Config(**(yaml.safe_load(path.read_text()) or {}))
