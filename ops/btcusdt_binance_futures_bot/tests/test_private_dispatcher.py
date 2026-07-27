from btcusdt_bot.private.consumer import PrivateEventDispatcher
from btcusdt_bot.state.store import StateStore


def test_private_dispatcher_updates_state_and_handles_expired_listen_key() -> None:
    store = StateStore()
    dispatcher = PrivateEventDispatcher(store)

    event_type = dispatcher.dispatch(
        {
            "e": "ACCOUNT_CONFIG_UPDATE",
            "E": 100,
            "ac": {"s": "BTCUSDT", "l": 3},
        }
    )
    assert event_type == "ACCOUNT_CONFIG_UPDATE"
    assert store.state.last_account_config_update["ac"]["l"] == 3
    assert store.state.last_private_event_type == "ACCOUNT_CONFIG_UPDATE"

    event_type = dispatcher.dispatch(
        {
            "e": "listenKeyExpired",
            "E": 200,
            "listenKey": "abc",
        }
    )
    assert event_type == "listenKeyExpired"
    assert store.state.listen_key_expired_at_ms == 200
    assert store.state.last_private_event_type == "listenKeyExpired"
