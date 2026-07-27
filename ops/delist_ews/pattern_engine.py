"""Delist EWS — движок паттернов делистинга.

Анализирует исторические паттерны Binance делистингов:
1. Monitoring Tag → делистинг через 30-90 дней (60% случаев)
2. Volume collapse → обычно предшествует на 2-4 недели
3. Team goes silent → GitHub/Twitter неактивны 90+ дней
4. Regulatory action → мгновенный риск
5. Smart money exit → крупные холдеры выводят

Scoring: каждый паттерн добавляет weight к risk_score.
При risk_score >= 0.6 → HIGH alert
При risk_score >= 0.8 → CRITICAL alert
"""
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import Optional
from loguru import logger


class RiskLevel(str, Enum):
    LOW = "low"           # 0.0 - 0.3
    MEDIUM = "medium"     # 0.3 - 0.6
    HIGH = "high"         # 0.6 - 0.8
    CRITICAL = "critical" # 0.8 - 1.0


@dataclass
class DelistSignal:
    """Единичный сигнал делистинга."""
    signal_type: str
    weight: float
    severity: str
    detail: str
    source: str = "market_data"
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()


@dataclass
class TokenRiskProfile:
    """Полный профиль риска токена."""
    symbol: str
    risk_score: float = 0.0
    risk_level: RiskLevel = RiskLevel.LOW
    signals: list[DelistSignal] = field(default_factory=list)
    recommendation: str = ""
    last_updated: str = ""

    def __post_init__(self):
        self.last_updated = datetime.utcnow().isoformat()
        self._update_level()

    def _update_level(self):
        if self.risk_score >= 0.8:
            self.risk_level = RiskLevel.CRITICAL
        elif self.risk_score >= 0.6:
            self.risk_level = RiskLevel.HIGH
        elif self.risk_score >= 0.3:
            self.risk_level = RiskLevel.MEDIUM
        else:
            self.risk_level = RiskLevel.LOW

    def add_signal(self, signal: DelistSignal):
        self.signals.append(signal)
        self.risk_score = min(
            sum(s.weight for s in self.signals), 1.0
        )
        self._update_level()
        self._update_recommendation()

    def _update_recommendation(self):
        if self.risk_level == RiskLevel.CRITICAL:
            self.recommendation = "НЕМЕДЛЕННО ПРОДАТЬ. Высочайший риск делистинга."
        elif self.risk_level == RiskLevel.HIGH:
            self.recommendation = "Закрыть позиции. Множественные сигналы делистинга."
        elif self.risk_level == RiskLevel.MEDIUM:
            self.recommendation = "Мониторить. Есть настораживающие сигналы."
        else:
            self.recommendation = "Нормальный статус."


# ═══════════════════════════════════════════════════════════════
# Исторические паттерны делистинга Binance (2023-2025)
# ═══════════════════════════════════════════════════════════════
HISTORICAL_DELIST_PATTERNS = {
    # Паттерн: Binance ставит Monitoring Tag
    "monitoring_tag": {
        "weight": 0.6,
        "description": "Monitoring Tag — в 60% случаев ведёт к делистингу за 30-90 дней",
        "examples": ["AKRO", "BLZ", "WRX", "VITE", "FIRO"],
        "avg_days_to_delist": 60,
    },
    # Паттерн: Объём торгов обваливается
    "volume_collapse": {
        "weight": 0.4,
        "description": "Объём -70%+ за 7 дней — маркетмейкеры уходят",
        "threshold": -0.7,
        "timeframe_days": 7,
    },
    # Паттерн: Ликвидность исчезает
    "liquidity_drain": {
        "weight": 0.5,
        "description": "Глубина стакана -50%+ — нет маркетмейкеров",
        "threshold": -0.5,
    },
    # Паттерн: Спред раздувается
    "spread_blowout": {
        "weight": 0.3,
        "description": "Спред 3x+ от нормы — токен становится неликвидным",
        "threshold_multiplier": 3.0,
    },
    # Паттерн: Команда неактивна
    "team_inactive": {
        "weight": 0.5,
        "description": "GitHub/Twitter неактивны 90+ дней",
        "days_threshold": 90,
    },
    # Паттерн: Регуляторное действие
    "regulatory_action": {
        "weight": 0.7,
        "description": "SEC/DOJ/регулятор упоминает проект",
    },
    # Паттерн: Smart money уходит
    "whale_exit": {
        "weight": 0.35,
        "description": "Крупные адреса выводят с бирж",
    },
    # Паттерн: Цена обваливается без общего падения рынка
    "isolated_crash": {
        "weight": 0.35,
        "description": "Цена -30%+ за 4ч при стабильном BTC",
    },
}


class PatternEngine:
    """Движок анализа паттернов делистинга."""

    def __init__(self):
        self.profiles: dict[str, TokenRiskProfile] = {}

    def analyze(self, symbol: str, market_signals: list[dict],
                announcement_signals: list[dict] = None) -> TokenRiskProfile:
        """Полный анализ риска делистинга для токена."""

        # Получаем или создаём профиль
        if symbol not in self.profiles:
            self.profiles[symbol] = TokenRiskProfile(symbol=symbol)
        profile = self.profiles[symbol]

        # Сброс сигналов для пересчёта
        profile.signals = []
        profile.risk_score = 0.0

        # Добавляем рыночные сигналы
        for sig in market_signals:
            profile.add_signal(DelistSignal(
                signal_type=sig["type"],
                weight=sig.get("weight", 0.2),
                severity=sig.get("severity", "medium"),
                detail=sig.get("detail", ""),
                source="market_data",
            ))

        # Добавляем сигналы из анонсов
        if announcement_signals:
            for sig in announcement_signals:
                profile.add_signal(DelistSignal(
                    signal_type=sig.get("type", "announcement"),
                    weight=0.8 if "delist" in sig.get("type", "") else 0.6,
                    severity=sig.get("severity", "high"),
                    detail=sig.get("title", ""),
                    source="binance_announcement",
                ))

        # Бонус за множественные сигналы (конвергенция)
        if len(profile.signals) >= 3:
            convergence_bonus = min(len(profile.signals) * 0.05, 0.15)
            profile.risk_score = min(profile.risk_score + convergence_bonus, 1.0)
            profile._update_level()
            profile._update_recommendation()

        logger.info(
            f"{symbol}: risk={profile.risk_score:.2f} "
            f"level={profile.risk_level.value} "
            f"signals={len(profile.signals)}"
        )

        return profile

    def get_high_risk_tokens(self) -> list[TokenRiskProfile]:
        """Получить все токены с HIGH/CRITICAL риском."""
        return [
            p for p in self.profiles.values()
            if p.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        ]

    def get_profile(self, symbol: str) -> Optional[TokenRiskProfile]:
        """Получить профиль токена."""
        return self.profiles.get(symbol)
