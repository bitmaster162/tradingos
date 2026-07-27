from __future__ import annotations

import abc
import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BotConfig:
    mode: str = "paper"
    network: str = "bsc-mainnet"
    chain_family: str = "evm"
    aggregator: str = "paper"
    router_mode: str = "aggregator_then_pancake_fallback"
    wallet_address: str = ""
    private_key: str = ""
    rpc_url: str = ""
    base_token: str = ""
    quote_token: str = "USDC"
    route_token: str = ""
    state_path: str = "ops/dex_range_bot/state.json"
    journal_path: str = "ops/dex_range_bot/journal.jsonl"
    loop_interval_sec: int = 30
    paper_price: float = 1.0
    lower: float = 0.85
    upper: float = 1.15
    level_count: int = 5
    take_profit_pct: float = 0.025
    order_size_quote: float = 100.0
    max_slippage_bps: int = 80
    max_price_impact_bps: int = 120
    max_gas_usd: float = 8.0
    min_pool_liquidity_usd: float = 50000.0
    kill_switch_break_pct: float = 0.03
    max_open_lots: int = 5
    deadline_sec: int = 90


@dataclass
class Quote:
    side: str
    price: float
    base_amount: float
    quote_amount: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Lot:
    slot_id: int
    amount_base: float
    amount_quote: float
    entry_price: float
    take_profit_price: float
    created_ts: int
    sold: bool = False


@dataclass
class OrderIntent:
    intent_id: str
    ts: int
    action: str
    network: str
    venue: str
    base_token: str
    quote_token: str
    route_token: str
    trigger_price: float
    quoted_price: float
    quote_amount: float
    base_amount: float
    max_slippage_bps: int
    max_price_impact_bps: int
    max_gas_usd: float
    deadline_sec: int
    lot_id: int
    reason: str
    status: str


class StateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"lots": [], "paused": False, "failed_exec_count": 0}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, state: dict[str, Any]) -> None:
        self.path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class JournalStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, payload: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


class DexAdapter(abc.ABC):
    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg

    async def __aenter__(self) -> "DexAdapter":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    @abc.abstractmethod
    async def get_mid_price(self) -> float:
        raise NotImplementedError

    @abc.abstractmethod
    async def get_quote_buy_base(
        self,
        quote_amount: float,
        slippage_bps: int,
    ) -> Quote:
        raise NotImplementedError

    @abc.abstractmethod
    async def get_quote_sell_base(
        self,
        base_amount: float,
        slippage_bps: int,
    ) -> Quote:
        raise NotImplementedError

    async def estimate_gas_usd(self, quote: Quote) -> float:
        return float(quote.metadata.get("gas_usd", 0.0))

    async def price_impact_bps(self, quote: Quote) -> float:
        return float(quote.metadata.get("price_impact_bps", 0.0))

    async def pool_liquidity_usd(self, quote: Quote) -> float:
        return float(quote.metadata.get("pool_liquidity_usd", 1_000_000.0))

    @abc.abstractmethod
    async def execute(self, quote: Quote) -> dict[str, Any]:
        raise NotImplementedError


class PaperAdapter(DexAdapter):
    async def get_mid_price(self) -> float:
        return float(os.getenv("PAPER_PRICE", self.cfg.paper_price))

    async def get_quote_buy_base(
        self,
        quote_amount: float,
        slippage_bps: int,
    ) -> Quote:
        price = await self.get_mid_price()
        base_amount = quote_amount / price
        return Quote(
            side="buy",
            price=price,
            base_amount=base_amount,
            quote_amount=quote_amount,
            metadata={
                "slippage_bps": slippage_bps,
                "gas_usd": 0.0,
                "price_impact_bps": 0.0,
                "pool_liquidity_usd": 1_000_000.0,
            },
        )

    async def get_quote_sell_base(
        self,
        base_amount: float,
        slippage_bps: int,
    ) -> Quote:
        price = await self.get_mid_price()
        quote_amount = base_amount * price
        return Quote(
            side="sell",
            price=price,
            base_amount=base_amount,
            quote_amount=quote_amount,
            metadata={
                "slippage_bps": slippage_bps,
                "gas_usd": 0.0,
                "price_impact_bps": 0.0,
                "pool_liquidity_usd": 1_000_000.0,
            },
        )

    async def execute(self, quote: Quote) -> dict[str, Any]:
        return {"status": "paper", "quote": asdict(quote)}


class AdapterSkeleton(DexAdapter):
    async def get_mid_price(self) -> float:
        raise NotImplementedError(
            "Replace AdapterSkeleton with a chain-specific adapter."
        )

    async def get_quote_buy_base(
        self,
        quote_amount: float,
        slippage_bps: int,
    ) -> Quote:
        raise NotImplementedError(
            "Implement aggregator quote fetching for the selected chain."
        )

    async def get_quote_sell_base(
        self,
        base_amount: float,
        slippage_bps: int,
    ) -> Quote:
        raise NotImplementedError(
            "Implement aggregator quote fetching for the selected chain."
        )

    async def execute(self, quote: Quote) -> dict[str, Any]:
        raise NotImplementedError(
            "Implement approvals, signing, broadcast, and receipt handling."
        )


class RangeStrategy:
    def __init__(
        self,
        cfg: BotConfig,
        adapter: DexAdapter,
        store: StateStore,
        journal: JournalStore,
    ) -> None:
        self.cfg = cfg
        self.adapter = adapter
        self.store = store
        self.journal = journal

    def _level_prices(self) -> list[float]:
        if self.cfg.level_count <= 1:
            return [self.cfg.lower]
        step = (self.cfg.upper - self.cfg.lower) / (self.cfg.level_count - 1)
        return [round(self.cfg.lower + i * step, 10) for i in range(self.cfg.level_count)]

    def _load_lots(self) -> tuple[dict[str, Any], list[Lot]]:
        state = self.store.load()
        lots = [Lot(**item) for item in state.get("lots", [])]
        return state, lots

    def _save_lots(self, state: dict[str, Any], lots: list[Lot]) -> None:
        state["lots"] = [asdict(lot) for lot in lots]
        self.store.save(state)

    async def cycle(self) -> None:
        state, lots = self._load_lots()
        price = await self.adapter.get_mid_price()

        if self._range_broken(price):
            state["paused"] = True
            self._save_lots(state, lots)
            payload = {"status": "paused", "reason": "kill_switch", "price": price}
            print(json.dumps(payload))
            self.journal.append(payload)
            return

        if state.get("paused"):
            payload = {"status": "paused", "reason": "manual_reset_required"}
            print(json.dumps(payload))
            self.journal.append(payload)
            return

        await self._try_open_lots(price, state, lots)
        await self._try_close_lots(price, state, lots)
        self._save_lots(state, lots)

    def _range_broken(self, price: float) -> bool:
        below = price < self.cfg.lower * (1 - self.cfg.kill_switch_break_pct)
        above = price > self.cfg.upper * (1 + self.cfg.kill_switch_break_pct)
        return below or above

    async def _try_open_lots(
        self,
        price: float,
        state: dict[str, Any],
        lots: list[Lot],
    ) -> None:
        active_slots = {lot.slot_id for lot in lots if not lot.sold}
        if len(active_slots) >= self.cfg.max_open_lots:
            return

        for slot_id, level in enumerate(self._level_prices()):
            if slot_id in active_slots:
                continue
            if price > level:
                continue

            quote = await self.adapter.get_quote_buy_base(
                quote_amount=self.cfg.order_size_quote,
                slippage_bps=self.cfg.max_slippage_bps,
            )
            if not await self._passes_filters(quote):
                continue

            result = await self.adapter.execute(quote)
            if result.get("status") not in {"paper", "success"}:
                state["failed_exec_count"] = int(state.get("failed_exec_count", 0)) + 1
                continue

            entry_price = quote.price
            intent = self._build_intent(
                action="BUY_LOT_INTENT",
                lot_id=slot_id,
                trigger_price=price,
                quote=quote,
                reason="range_dip_buy",
                status=result.get("status", "created"),
            )
            lots.append(
                Lot(
                    slot_id=slot_id,
                    amount_base=quote.base_amount,
                    amount_quote=quote.quote_amount,
                    entry_price=entry_price,
                    take_profit_price=entry_price * (1 + self.cfg.take_profit_pct),
                    created_ts=int(time.time()),
                )
            )
            payload = asdict(intent)
            print(json.dumps(payload))
            self.journal.append(payload)

    async def _try_close_lots(
        self,
        price: float,
        state: dict[str, Any],
        lots: list[Lot],
    ) -> None:
        for lot in lots:
            if lot.sold or price < lot.take_profit_price:
                continue

            quote = await self.adapter.get_quote_sell_base(
                base_amount=lot.amount_base,
                slippage_bps=self.cfg.max_slippage_bps,
            )
            if not await self._passes_filters(quote):
                continue

            result = await self.adapter.execute(quote)
            if result.get("status") not in {"paper", "success"}:
                state["failed_exec_count"] = int(state.get("failed_exec_count", 0)) + 1
                continue

            lot.sold = True
            intent = self._build_intent(
                action="SELL_LOT_INTENT",
                lot_id=lot.slot_id,
                trigger_price=price,
                quote=quote,
                reason="take_profit_hit",
                status=result.get("status", "created"),
            )
            payload = asdict(intent)
            print(json.dumps(payload))
            self.journal.append(payload)

    async def _passes_filters(self, quote: Quote) -> bool:
        gas_usd = await self.adapter.estimate_gas_usd(quote)
        impact_bps = await self.adapter.price_impact_bps(quote)
        liquidity_usd = await self.adapter.pool_liquidity_usd(quote)
        if gas_usd > self.cfg.max_gas_usd:
            return False
        if impact_bps > self.cfg.max_price_impact_bps:
            return False
        if liquidity_usd < self.cfg.min_pool_liquidity_usd:
            return False
        return True

    def _build_intent(
        self,
        action: str,
        lot_id: int,
        trigger_price: float,
        quote: Quote,
        reason: str,
        status: str,
    ) -> OrderIntent:
        return OrderIntent(
            intent_id=f"{action.lower()}-{lot_id}-{int(time.time())}",
            ts=int(time.time()),
            action=action,
            network=self.cfg.network,
            venue=self.cfg.router_mode if self.cfg.router_mode else self.cfg.aggregator,
            base_token=self.cfg.base_token,
            quote_token=self.cfg.quote_token,
            route_token=self.cfg.route_token,
            trigger_price=trigger_price,
            quoted_price=quote.price,
            quote_amount=quote.quote_amount,
            base_amount=quote.base_amount,
            max_slippage_bps=self.cfg.max_slippage_bps,
            max_price_impact_bps=self.cfg.max_price_impact_bps,
            max_gas_usd=self.cfg.max_gas_usd,
            deadline_sec=self.cfg.deadline_sec,
            lot_id=lot_id,
            reason=reason,
            status=status,
        )


def build_config() -> BotConfig:
    return BotConfig(
        mode=os.getenv("BOT_MODE", "paper"),
        network=os.getenv("NETWORK", "bsc-mainnet"),
        chain_family=os.getenv("CHAIN_FAMILY", "evm"),
        aggregator=os.getenv("AGGREGATOR", "paper"),
        router_mode=os.getenv("ROUTER_MODE", "aggregator_then_pancake_fallback"),
        wallet_address=os.getenv("WALLET_ADDRESS", ""),
        private_key=os.getenv("PRIVATE_KEY", ""),
        rpc_url=os.getenv("RPC_URL", ""),
        base_token=os.getenv("BASE_TOKEN", ""),
        quote_token=os.getenv("QUOTE_TOKEN", "USDC"),
        route_token=os.getenv("ROUTE_TOKEN", ""),
        state_path=os.getenv("STATE_PATH", "ops/dex_range_bot/state.json"),
        journal_path=os.getenv("JOURNAL_PATH", "ops/dex_range_bot/journal.jsonl"),
        loop_interval_sec=int(os.getenv("LOOP_INTERVAL_SEC", "30")),
        paper_price=float(os.getenv("PAPER_PRICE", "1.0")),
        lower=float(os.getenv("LOWER", "0.85")),
        upper=float(os.getenv("UPPER", "1.15")),
        level_count=int(os.getenv("LEVEL_COUNT", "5")),
        take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "0.025")),
        order_size_quote=float(os.getenv("ORDER_SIZE_QUOTE", "100.0")),
        max_slippage_bps=int(os.getenv("MAX_SLIPPAGE_BPS", "80")),
        max_price_impact_bps=int(os.getenv("MAX_PRICE_IMPACT_BPS", "120")),
        max_gas_usd=float(os.getenv("MAX_GAS_USD", "8.0")),
        min_pool_liquidity_usd=float(os.getenv("MIN_POOL_LIQUIDITY_USD", "50000")),
        kill_switch_break_pct=float(os.getenv("KILL_SWITCH_BREAK_PCT", "0.03")),
        max_open_lots=int(os.getenv("MAX_OPEN_LOTS", "5")),
        deadline_sec=int(os.getenv("DEADLINE_SEC", "90")),
    )


def build_adapter(cfg: BotConfig) -> DexAdapter:
    if cfg.mode == "paper" or cfg.aggregator == "paper":
        return PaperAdapter(cfg)
    return AdapterSkeleton(cfg)


async def main() -> None:
    cfg = build_config()
    store = StateStore(cfg.state_path)
    journal = JournalStore(cfg.journal_path)
    async with build_adapter(cfg) as adapter:
        strategy = RangeStrategy(cfg, adapter, store, journal)
        while True:
            try:
                await strategy.cycle()
            except Exception as exc:
                payload = {"status": "cycle_error", "error": str(exc)}
                print(json.dumps(payload))
                journal.append(payload)
            await asyncio.sleep(cfg.loop_interval_sec)


if __name__ == "__main__":
    asyncio.run(main())
