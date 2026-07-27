from __future__ import annotations

from urllib.parse import quote_plus


def build_combined_stream_url(base_url: str, streams: list[str]) -> str:
    base = base_url.rstrip("/")
    joined = "/".join(streams)
    return f"{base}/stream?streams={joined}"


def build_single_stream_url(base_url: str, stream: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/ws/{stream}"


def build_private_url(base_url: str, listen_key: str, events: list[str] | None = None) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/private"):
        events = events or ["ORDER_TRADE_UPDATE", "ACCOUNT_UPDATE", "ALGO_UPDATE"]
        encoded_events = "/".join(quote_plus(event) for event in events)
        return f"{base}/ws?listenKey={quote_plus(listen_key)}&events={encoded_events}"
    # Legacy listenKey route:
    return f"{base}/ws/{quote_plus(listen_key)}"
