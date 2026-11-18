from datetime import date

import pytest

from clipper import ops
from clipper.config import Campaign, Config
from clipper.ingest import ClipperError

CFG = Config(campaigns=[Campaign(name="gta", streamers=["penta"], platforms=["tiktok", "youtube"],
                                 required_tags=["#ad", "@penta"], rate_per_1k=1.0, cap_per_clip=50)])


def _manifest(n: int) -> dict:
    return {"vod": {"streamer": "penta"},
            "clips": [{"rank": i, "file": f"out/c{i}.mp4", "title": "x" * 120, "caption": "lol #GTA6", "category": "funny"}
                      for i in range(1, n + 1)]}


def test_schedule_unique_per_platform_and_daily_cap(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    rows = ops.build_schedule(_manifest(25), CFG, ["tiktok", "youtube", "instagram"], "18:00", "45m", 10, date(2026, 11, 19))
    assert {r["platform"] for r in rows} == {"tiktok", "youtube"}  # instagram not in campaign
    for p in ("tiktok", "youtube"):
        pr = [r for r in rows if r["platform"] == p]
        assert len({r["file"] for r in pr}) == len(pr) == 25
        per_day = {}
        for r in pr:
            per_day[r["post_at"][:10]] = per_day.get(r["post_at"][:10], 0) + 1
        assert max(per_day.values()) <= 10
    yt = [r for r in rows if r["platform"] == "youtube"]
    assert all(len(r["title"]) <= 100 and r["title"].endswith("#Shorts #GTA6") for r in yt)


def test_schedule_skips_already_posted(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "c1.mp4").touch()
    ops.track_add(tmp_path / "out" / "c1.mp4", "tiktok", "https://tiktok.com/1", CFG, True)
    with pytest.raises(ClipperError):
        ops.track_add(tmp_path / "out" / "c1.mp4", "tiktok", "https://tiktok.com/2", CFG, True)
    rows = ops.build_schedule(_manifest(3), CFG, ["tiktok"], "18:00", "45m", 10, date(2026, 11, 19))
    assert [r["rank"] for r in rows] == [2, 3]


def test_campaign_rules() -> None:
    assert ops.with_tags("lol #GTA6 #AD", ["#ad", "@penta"]) == "lol #GTA6 #AD @penta"
    assert ops.earnings(200_000, CFG.campaigns[0]) == 50  # capped
    assert ops.require_campaign(CFG, "Penta").name == "gta"
    with pytest.raises(ClipperError):
        ops.require_campaign(CFG, "someoneelse")
