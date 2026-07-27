from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None else default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value is not None and value != "" else default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value is not None and value != "" else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: str) -> tuple[str, ...]:
    raw = _env_str(name, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class BotConfig:
    env: str
    symbol: str
    rest_base_url: str
    ws_public_base_url: str
    ws_market_base_url: str
    ws_private_base_url: str
    api_key: str
    api_secret: str
    recv_window_ms: int
    timeout_s: float
    position_mode: str
    margin_mode: str
    max_leverage: int
    max_position_notional_usdt: float
    max_daily_loss_usdt: float
    max_normal_open_orders: int
    max_algo_open_orders: int
    stale_data_limit_ms: int
    countdown_cancel_ms: int
    heartbeat_interval_ms: int
    user_stream_keepalive_ms: int
    reconnect_initial_backoff_ms: int
    reconnect_max_backoff_ms: int
    kline_intervals: tuple[str, ...]
    private_events: tuple[str, ...]
    enable_contract_info_stream: bool
    enable_force_order_stream: bool
    enable_countdown_heartbeat: bool
    state_flush_every_events: int
    data_dir: Path
    log_level: str

    @classmethod
    def from_env(cls) -> "BotConfig":
        env = _env_str("BOT_ENV", "demo").lower()
        live = env == "live"

        rest_base_url = _env_str(
            "BOT_REST_BASE_URL",
            "https://fapi.binance.com" if live else "https://demo-fapi.binance.com",
        )

        # Live docs now expose routed /public /market /private websocket bases.
        # Demo docs for UM futures explicitly expose the root websocket base.
        ws_public_base_url = _env_str(
            "BOT_WS_PUBLIC_BASE_URL",
            "wss://fstream.binance.com/public" if live else "wss://fstream.binancefuture.com",
        )
        ws_market_base_url = _env_str(
            "BOT_WS_MARKET_BASE_URL",
            "wss://fstream.binance.com/market" if live else "wss://fstream.binancefuture.com",
        )
        ws_private_base_url = _env_str(
            "BOT_WS_PRIVATE_BASE_URL",
            "wss://fstream.binance.com/private" if live else "wss://fstream.binancefuture.com",
        )

        data_dir = Path(_env_str("DATA_DIR", "./data"))
        data_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            env=env,
            symbol=_env_str("BOT_SYMBOL", "BTCUSDT").upper(),
            rest_base_url=rest_base_url.rstrip("/"),
            ws_public_base_url=ws_public_base_url.rstrip("/"),
            ws_market_base_url=ws_market_base_url.rstrip("/"),
            ws_private_base_url=ws_private_base_url.rstrip("/"),
            api_key=_env_str("BINANCE_API_KEY"),
            api_secret=_env_str("BINANCE_API_SECRET"),
            recv_window_ms=_env_int("BINANCE_RECV_WINDOW_MS", 5000),
            timeout_s=_env_float("BINANCE_TIMEOUT_S", 10.0),
            position_mode=_env_str("POSITION_MODE", "ONE_WAY").upper(),
            margin_mode=_env_str("MARGIN_MODE", "ISOLATED").upper(),
            max_leverage=_env_int("MAX_LEVERAGE", 3),
            max_position_notional_usdt=_env_float("MAX_POSITION_NOTIONAL_USDT", 500.0),
            max_daily_loss_usdt=_env_float("MAX_DAILY_LOSS_USDT", 50.0),
            max_normal_open_orders=_env_int("MAX_NORMAL_OPEN_ORDERS", 8),
            max_algo_open_orders=_env_int("MAX_ALGO_OPEN_ORDERS", 20),
            stale_data_limit_ms=_env_int("STALE_DATA_LIMIT_MS", 4000),
            countdown_cancel_ms=_env_int("COUNTDOWN_CANCEL_MS", 120000),
            heartbeat_interval_ms=_env_int("HEARTBEAT_INTERVAL_MS", 30000),
            user_stream_keepalive_ms=_env_int("USER_STREAM_KEEPALIVE_MS", 1800000),
            reconnect_initial_backoff_ms=_env_int("RECONNECT_INITIAL_BACKOFF_MS", 1000),
            reconnect_max_backoff_ms=_env_int("RECONNECT_MAX_BACKOFF_MS", 30000),
            kline_intervals=_env_csv("KLINE_INTERVALS", "1m,5m"),
            private_events=_env_csv(
                "PRIVATE_EVENTS",
                "ORDER_TRADE_UPDATE,ACCOUNT_UPDATE,ALGO_UPDATE,ACCOUNT_CONFIG_UPDATE,listenKeyExpired",
            ),
            enable_contract_info_stream=_env_bool("ENABLE_CONTRACT_INFO_STREAM", True),
            enable_force_order_stream=_env_bool("ENABLE_FORCE_ORDER_STREAM", False),
            enable_countdown_heartbeat=_env_bool("ENABLE_COUNTDOWN_HEARTBEAT", False),
            state_flush_every_events=max(1, _env_int("STATE_FLUSH_EVERY_EVENTS", 1)),
            data_dir=data_dir,
            log_level=_env_str("LOG_LEVEL", "INFO").upper(),
        )

    @property
    def has_api_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def is_live(self) -> bool:
        return self.env == "live"
