from types import SimpleNamespace

import pytest

from clipper import judge as J

GOOD = '{"score": 8, "category": "funny", "start": 2, "end": 30, "hook": "HE DID NOT", "title": "t", "caption": "c #GTA6", "reason": "r"}'


def test_parse_handles_fences_and_prose() -> None:
    assert J.parse_judgment(f"Sure!\n```json\n{GOOD}\n```").score == 8


@pytest.mark.parametrize("bad", ["no json here", "{not: valid}", GOOD.replace('"score": 8', '"score": 11'),
                                 GOOD.replace("funny", "spicy"), '{"score": 5}'])
def test_parse_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        J.parse_judgment(bad)


def _fake_client(replies: list[str]) -> SimpleNamespace:
    calls = []

    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=replies[len(calls) - 1])])

    return SimpleNamespace(messages=SimpleNamespace(create=create), calls=calls)


CAND = {"start": 100, "end": 135, "fused": 3.0, "chat": 1.0, "audio": 2.0}


def test_judge_retries_once_then_succeeds(monkeypatch) -> None:
    fake = _fake_client(["oops", GOOD])
    monkeypatch.setattr(J, "_client", lambda: fake)
    assert J.judge(CAND, [], [], "penta", "m").score == 8
    assert len(fake.calls) == 2 and "invalid" in fake.calls[1]["messages"][0]["content"]


def test_judge_gives_up_after_two_bad_replies(monkeypatch) -> None:
    monkeypatch.setattr(J, "_client", lambda: _fake_client(["oops", "still oops"]))
    assert J.judge(CAND, [], [], "penta", "m") is None
