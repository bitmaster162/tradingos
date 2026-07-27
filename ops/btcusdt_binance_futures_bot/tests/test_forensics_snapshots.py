import json

from btcusdt_bot.forensics.snapshots import ForensicsRecorder
from btcusdt_bot.storage.jsonl import JSONLWriter


def test_forensics_recorder_writes_snapshot_jsonl(tmp_path) -> None:
    with JSONLWriter(tmp_path) as writer:
        recorder = ForensicsRecorder(writer, symbol="BTCUSDT")
        path = recorder.record_snapshot(
            action_type="entry_submit",
            payload={"clientOrderId": "abc"},
            event_time_ms=1_700_000_000_000,
            state_before="IDLE",
            state_after="ENTRY_PENDING",
            decision="entry_submit",
            active_entry_client_id="abc",
            market_messages=42,
            tags=("test",),
        )

    payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["symbol"] == "BTCUSDT"
    assert payload["action_type"] == "entry_submit"
    assert payload["state_before"] == "IDLE"
    assert payload["state_after"] == "ENTRY_PENDING"
    assert payload["payload"]["clientOrderId"] == "abc"
    assert payload["tags"] == ["test"]
