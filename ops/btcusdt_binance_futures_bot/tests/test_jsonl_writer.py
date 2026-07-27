import json

from btcusdt_bot.storage.jsonl import JSONLWriter


def test_jsonl_writer_appends_record_into_dated_bucket(tmp_path) -> None:
    writer = JSONLWriter(tmp_path)
    path = writer.append_record(
        "market",
        "btcusdt@aggTrade",
        {"stream": "btcusdt@aggTrade", "payload": {"e": "aggTrade", "q": "1"}},
        event_time_ms=1712361600000,
    )
    writer.close()

    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["stream"] == "btcusdt@aggTrade"
    assert payload["payload"]["e"] == "aggTrade"
