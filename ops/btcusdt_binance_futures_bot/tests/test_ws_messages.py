from btcusdt_bot.ws.messages import decode_ws_message


def test_decode_combined_market_message() -> None:
    message = decode_ws_message(
        '{"stream":"btcusdt@aggTrade","data":{"e":"aggTrade","E":1,"s":"BTCUSDT","q":"1"}}'
    )

    assert message.stream == "btcusdt@aggTrade"
    assert message.event_type == "aggTrade"
    assert message.payload["s"] == "BTCUSDT"


def test_decode_raw_private_message() -> None:
    message = decode_ws_message(
        '{"e":"ACCOUNT_UPDATE","E":10,"a":{"m":"ORDER","B":[],"P":[]}}'
    )

    assert message.stream is None
    assert message.event_type == "ACCOUNT_UPDATE"
    assert message.payload["a"]["m"] == "ORDER"
