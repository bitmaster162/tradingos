"""Delist EWS — SQLite хранилище для метрик и алертов."""
import aiosqlite
import json
from datetime import datetime, timezone
from typing import Optional
from loguru import logger

from config import settings


DB_PATH = settings.db_path


async def init_db():
    """Создание таблиц при первом запуске."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS token_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                price REAL,
                volume_24h REAL,
                volume_change_pct REAL,
                bid_depth REAL,
                ask_depth REAL,
                spread_pct REAL,
                market_cap REAL,
                signal_count INTEGER DEFAULT 0,
                signals TEXT DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'medium',
                message TEXT NOT NULL,
                signals TEXT DEFAULT '[]',
                sent_telegram BOOLEAN DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT,
                announcement_type TEXT,
                symbols TEXT DEFAULT '[]',
                raw_data TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(title)
            );

            CREATE TABLE IF NOT EXISTS watchlist (
                symbol TEXT PRIMARY KEY,
                reason TEXT,
                risk_score REAL DEFAULT 0.0,
                added_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_checked TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_symbol ON token_snapshots(symbol);
            CREATE INDEX IF NOT EXISTS idx_snapshots_created ON token_snapshots(created_at);
            CREATE INDEX IF NOT EXISTS idx_alerts_symbol ON alerts(symbol);
            CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts(alert_type);
        """)
        await db.commit()
        logger.info("Database initialized")


async def save_snapshot(symbol: str, price: float, volume_24h: float,
                        volume_change_pct: float, bid_depth: float,
                        ask_depth: float, spread_pct: float,
                        signal_count: int = 0, signals: list = None):
    """Сохранить снимок метрик токена."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO token_snapshots
               (symbol, price, volume_24h, volume_change_pct, bid_depth,
                ask_depth, spread_pct, signal_count, signals)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, price, volume_24h, volume_change_pct, bid_depth,
             ask_depth, spread_pct, signal_count, json.dumps(signals or []))
        )
        await db.commit()


async def save_alert(symbol: str, alert_type: str, severity: str,
                     message: str, signals: list = None):
    """Сохранить алерт."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO alerts (symbol, alert_type, severity, message, signals)
               VALUES (?, ?, ?, ?, ?)""",
            (symbol, alert_type, severity, message, json.dumps(signals or []))
        )
        await db.commit()


async def save_announcement(title: str, url: str = None,
                            announcement_type: str = None,
                            symbols: list = None, raw_data: dict = None):
    """Сохранить анонс Binance (с дедупликацией по title)."""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                """INSERT OR IGNORE INTO announcements
                   (title, url, announcement_type, symbols, raw_data)
                   VALUES (?, ?, ?, ?, ?)""",
                (title, url, announcement_type,
                 json.dumps(symbols or []), json.dumps(raw_data or {}))
            )
            await db.commit()
            return True
        except Exception:
            return False


async def get_previous_snapshot(symbol: str, hours_ago: int = 24):
    """Получить предыдущий снимок для сравнения."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM token_snapshots
               WHERE symbol = ? AND created_at <= datetime('now', ?)
               ORDER BY created_at DESC LIMIT 1""",
            (symbol, f"-{hours_ago} hours")
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def add_to_watchlist(symbol: str, reason: str, risk_score: float = 0.0):
    """Добавить токен в watchlist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO watchlist (symbol, reason, risk_score, added_at)
               VALUES (?, ?, ?, datetime('now'))""",
            (symbol, reason, risk_score)
        )
        await db.commit()


async def get_watchlist():
    """Получить весь watchlist."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM watchlist ORDER BY risk_score DESC"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_recent_alerts(symbol: str = None, hours: int = 24):
    """Получить последние алерты."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if symbol:
            cursor = await db.execute(
                """SELECT * FROM alerts
                   WHERE symbol = ? AND created_at >= datetime('now', ?)
                   ORDER BY created_at DESC""",
                (symbol, f"-{hours} hours")
            )
        else:
            cursor = await db.execute(
                """SELECT * FROM alerts
                   WHERE created_at >= datetime('now', ?)
                   ORDER BY created_at DESC""",
                (f"-{hours} hours",)
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
