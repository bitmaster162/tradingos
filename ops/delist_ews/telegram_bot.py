"""Delist EWS — Telegram бот для алертов и управления.

Команды:
  /status — текущий статус мониторинга
  /watchlist — список отслеживаемых токенов
  /check <SYMBOL> — проверить конкретный токен
  /alerts — последние алерты
  /risk <SYMBOL> — профиль риска токена
  /scan — запустить полное сканирование
  /add <SYMBOL> — добавить в watchlist
  /remove <SYMBOL> — убрать из watchlist
"""
import asyncio
from datetime import datetime
from loguru import logger

from config import settings
from database import get_watchlist, get_recent_alerts, add_to_watchlist
from pattern_engine import TokenRiskProfile, RiskLevel


# ═══════════════════════════════════════════════════════════════
# Форматирование сообщений
# ═══════════════════════════════════════════════════════════════

RISK_EMOJI = {
    RiskLevel.LOW: "🟢",
    RiskLevel.MEDIUM: "🟡",
    RiskLevel.HIGH: "🟠",
    RiskLevel.CRITICAL: "🔴",
}

SIGNAL_EMOJI = {
    "volume_collapse": "📉",
    "liquidity_drain": "🏜️",
    "spread_blowout": "📊",
    "price_crash": "💥",
    "monitoring_tag": "🏷️",
    "regulatory_action": "⚖️",
    "whale_exit": "🐋",
    "announcement_delist": "🚨",
    "announcement_warning": "⚠️",
}


def format_alert(profile: TokenRiskProfile) -> str:
    """Форматировать алерт для Telegram."""
    emoji = RISK_EMOJI.get(profile.risk_level, "⚪")
    severity = profile.risk_level.value.upper()

    lines = [
        f"{emoji} *DELIST EWS — {severity}*",
        f"",
        f"*Токен:* `{profile.symbol}`",
        f"*Risk Score:* {profile.risk_score:.0%}",
        f"*Рекомендация:* {profile.recommendation}",
        f"",
        f"*Сигналы ({len(profile.signals)}):*",
    ]

    for sig in profile.signals:
        sig_emoji = SIGNAL_EMOJI.get(sig.signal_type, "•")
        lines.append(f"  {sig_emoji} {sig.detail}")

    lines.extend([
        f"",
        f"_Время: {datetime.utcnow().strftime('%H:%M UTC %d.%m.%Y')}_",
    ])

    return "\n".join(lines)


def format_watchlist(watchlist: list[dict]) -> str:
    """Форматировать watchlist для Telegram."""
    if not watchlist:
        return "📋 Watchlist пуст. Добавь токен: /add SYMBOL"

    lines = ["📋 *Watchlist — Delist EWS*", ""]
    for item in watchlist:
        score = item.get("risk_score", 0)
        if score >= 0.8:
            emoji = "🔴"
        elif score >= 0.6:
            emoji = "🟠"
        elif score >= 0.3:
            emoji = "🟡"
        else:
            emoji = "🟢"
        lines.append(
            f"{emoji} `{item['symbol']}` — risk {score:.0%} — _{item.get('reason', 'manual')}_"
        )

    return "\n".join(lines)


def format_status(stats: dict) -> str:
    """Форматировать статус системы."""
    return "\n".join([
        "🛡️ *Delist EWS — Status*",
        "",
        f"*Uptime:* {stats.get('uptime', 'N/A')}",
        f"*Watchlist:* {stats.get('watchlist_count', 0)} токенов",
        f"*Last scan:* {stats.get('last_scan', 'never')}",
        f"*Alerts (24h):* {stats.get('alerts_24h', 0)}",
        f"*High risk:* {stats.get('high_risk_count', 0)} токенов",
        "",
        f"*Interval:* каждые {settings.check_interval_seconds}s",
        f"*Thresholds:*",
        f"  Volume drop: {settings.volume_drop_threshold:.0%}",
        f"  Liquidity drop: {settings.liquidity_drop_threshold:.0%}",
        f"  Spread spike: {settings.spread_spike_threshold:.0f}x",
    ])


def format_risk_profile(profile: TokenRiskProfile) -> str:
    """Детальный профиль риска."""
    emoji = RISK_EMOJI.get(profile.risk_level, "⚪")

    lines = [
        f"{emoji} *Risk Profile — {profile.symbol}*",
        f"",
        f"*Risk Score:* {profile.risk_score:.0%} ({profile.risk_level.value})",
        f"*Рекомендация:* {profile.recommendation}",
        f"*Updated:* {profile.last_updated[:19]}",
        f"",
    ]

    if profile.signals:
        lines.append(f"*Сигналы ({len(profile.signals)}):*")
        for sig in profile.signals:
            sig_emoji = SIGNAL_EMOJI.get(sig.signal_type, "•")
            lines.append(
                f"  {sig_emoji} *{sig.signal_type}* (weight: {sig.weight:.0%})"
            )
            lines.append(f"      {sig.detail}")
            lines.append(f"      _Source: {sig.source}_")
    else:
        lines.append("✅ Сигналов не обнаружено")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Отправка сообщений
# ═══════════════════════════════════════════════════════════════

async def send_telegram_message(text: str, chat_id: str = None,
                                 parse_mode: str = "Markdown"):
    """Отправить сообщение в Telegram."""
    import httpx

    token = settings.telegram_bot_token
    target_chat = chat_id or settings.telegram_chat_id

    if not token or not target_chat:
        logger.warning("Telegram not configured, skipping message")
        return False

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": target_chat,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                }
            )
            if resp.status_code == 200:
                logger.info(f"Telegram message sent to {target_chat}")
                return True
            else:
                logger.error(f"Telegram error: {resp.status_code} {resp.text}")
                return False
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


async def send_alert(profile: TokenRiskProfile):
    """Отправить алерт в Telegram."""
    text = format_alert(profile)
    return await send_telegram_message(text)


async def send_daily_report(profiles: list[TokenRiskProfile],
                             stats: dict):
    """Ежедневный отчёт."""
    lines = [
        "📊 *Delist EWS — Daily Report*",
        f"_{datetime.utcnow().strftime('%d.%m.%Y')}_",
        "",
        f"*Сканировано:* {stats.get('scanned', 0)} токенов",
        f"*Алертов за 24ч:* {stats.get('alerts_24h', 0)}",
        "",
    ]

    critical = [p for p in profiles if p.risk_level == RiskLevel.CRITICAL]
    high = [p for p in profiles if p.risk_level == RiskLevel.HIGH]

    if critical:
        lines.append(f"🔴 *CRITICAL ({len(critical)}):*")
        for p in critical:
            lines.append(f"  `{p.symbol}` — {p.risk_score:.0%}")

    if high:
        lines.append(f"🟠 *HIGH ({len(high)}):*")
        for p in high:
            lines.append(f"  `{p.symbol}` — {p.risk_score:.0%}")

    if not critical and not high:
        lines.append("✅ Нет токенов с высоким риском делистинга")

    await send_telegram_message("\n".join(lines))
