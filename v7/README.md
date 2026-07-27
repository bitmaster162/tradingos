# Robert Trade System v7

Статус в unified-пакете: `bundled snapshot`

В этот архив включён уже собранный слой `v7/`:
- `faq_seeds.md`
- `oco_recipes.csv`
- `alerts_rules.json`
- `rule_engine_template.py`
- `regex_test_sample.txt`
- `live_checklist_printable.md`
- `risk_of_ruin_sim.py`
- `motif_cooccurrence_v7.csv`
- `motif_cooccurrence_v7.png`

## Что можно запускать прямо здесь

```bash
python v7/rule_engine_template.py v7/regex_test_sample.txt --rules v7/alerts_rules.json --pretty
python v7/risk_of_ruin_sim.py 0.45 2.0 -1.0 0.5 1000 20000
```

## Что важно понимать

- Это bundled snapshot, а не полный source corpus.
- `build_v7.py` и большие входные research-данные сюда не включены специально, чтобы архив оставался компактным.
- Если нужен полный воспроизводимый rebuild `v7`, его надо делать из исходного workspace, где лежит сырьевой корпус.
