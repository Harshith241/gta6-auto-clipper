import json

import pytest

from clipper.ingest import ClipperError, import_chat, parse_time


def test_parse_time() -> None:
    assert parse_time("2h") == 7200
    assert parse_time("1h30m") == 5400
    assert parse_time("00:12:30") == 750
    assert parse_time("45") == 45
    with pytest.raises(ClipperError):
        parse_time("soon")


def test_import_twitchdownloader_chat_with_offset(tmp_path) -> None:
    src = tmp_path / "c.json"
    src.write_text(json.dumps({"comments": [
        {"content_offset_seconds": 50, "commenter": {"display_name": "a"}, "message": {"body": "early"}},
        {"content_offset_seconds": 130, "commenter": {"display_name": "b"}, "message": {"body": "KEKW"}},
    ]}))
    import_chat(src, tmp_path / "chat.jsonl", offset=100)
    rows = [json.loads(line) for line in (tmp_path / "chat.jsonl").read_text().splitlines()]
    assert rows == [{"t": 30, "author": "b", "text": "KEKW"}]
