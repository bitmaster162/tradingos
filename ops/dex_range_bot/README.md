# DEX Range Bot MVP

Этот модуль хранит минимальный каркас `on-chain` range-bot для DEX.

Что внутри:

- `dex_range_bot_mvp.py` - strategy loop, lot accounting, paper/live adapter interface
- `.env.example` - базовые переменные окружения
- `.env.bnb.example` - BNB Chain defaults
- `bnb_order_intent.example.json` - example order intent for BNB
- `requirements.txt` - пустой minimal placeholder; live adapters добавляются под конкретную сеть

BNB-specific layer:

- `docs/DEX_RANGE_BOT_BNB_v1_1.md`
- `configs/DEX_RANGE_BOT_BNB_v1_1.json`

Важно:

- из коробки production-live execution не обещается
- сначала `paper`, потом chain-specific adapter
- актуальные API агрегаторов проверять перед live
