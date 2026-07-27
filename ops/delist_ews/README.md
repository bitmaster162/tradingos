# Delist EWS — Early Warning System

Система раннего предупреждения о делистингах Binance.

## Запуск

```bash
# 1. Конфиг
cp .env.example .env
# Впиши TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID

# 2. Установка
pip install -r requirements.txt

# 3. Запуск
python main.py                  # Непрерывный мониторинг
python main.py --scan-once      # Одно сканирование
python main.py --check AKRO     # Проверить токен
python main.py --watchlist       # Показать watchlist
```

## Docker

```bash
docker build -t delist-ews .
docker run -d --env-file .env -v ./data:/app/data delist-ews
```

## Сигналы

| Сигнал | Вес | Описание |
|--------|-----|----------|
| monitoring_tag | 0.6 | Binance Monitoring Tag |
| volume_collapse | 0.4 | Объём -70% за 7 дней |
| liquidity_drain | 0.5 | Глубина стакана -50% |
| spread_blowout | 0.3 | Спред 3x от нормы |
| regulatory_action | 0.7 | Регуляторное действие |
| announcement_delist | 0.8 | Анонс делистинга |
