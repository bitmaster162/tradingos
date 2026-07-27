from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEX_MODULE = ROOT / "ops" / "dex_range_bot" / "dex_range_bot_mvp.py"


def load_dex_module():
    spec = importlib.util.spec_from_file_location("dex_range_bot_mvp", DEX_MODULE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {DEX_MODULE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def run() -> None:
    dex = load_dex_module()
    state_path = ROOT / "_dl" / "smoke" / "dex_state.json"
    journal_path = ROOT / "_dl" / "smoke" / "dex_journal.jsonl"
    state_path.unlink(missing_ok=True)
    journal_path.unlink(missing_ok=True)

    cfg = dex.BotConfig(
        mode="paper",
        aggregator="paper",
        network="bsc-mainnet-paper",
        base_token="DEMO",
        quote_token="USDT",
        route_token="WBNB",
        state_path=str(state_path),
        journal_path=str(journal_path),
        lower=0.85,
        upper=1.15,
        level_count=3,
        take_profit_pct=0.025,
        order_size_quote=100.0,
        max_open_lots=3,
    )
    store = dex.StateStore(cfg.state_path)
    journal = dex.JournalStore(cfg.journal_path)
    async with dex.PaperAdapter(cfg) as adapter:
        strategy = dex.RangeStrategy(cfg, adapter, store, journal)
        os.environ["PAPER_PRICE"] = "0.84"
        await strategy.cycle()
        os.environ["PAPER_PRICE"] = "0.90"
        await strategy.cycle()

    print(f"DEX paper smoke completed: {state_path}")
    print(f"DEX paper journal: {journal_path}")


if __name__ == "__main__":
    asyncio.run(run())
