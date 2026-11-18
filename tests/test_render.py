from clipper.render import ass_script


def test_ass_highlights_one_word_per_event_and_clips_range() -> None:
    words = [{"w": w, "s": 10 + i * 0.4, "e": 10.3 + i * 0.4} for i, w in enumerate("no way he did {that} lol".split())]
    words.append({"w": "outside", "s": 50, "e": 51})
    ass = ass_script(words, start=10, end=20, hook="NO WAY", cap_y=672, hook_y=900)
    caps = [line for line in ass.splitlines() if ",Cap," in line]
    assert len(caps) == 6  # one event per in-range word
    assert all(line.count(r"\c&H00FFFF&") == 1 for line in caps)
    assert "OUTSIDE" not in ass and "{THAT}" not in ass
    assert any(",Hook," in line and "NO WAY" in line for line in ass.splitlines())
