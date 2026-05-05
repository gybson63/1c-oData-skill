#!/usr/bin/env python3
"""Метрики и трекинг использования для проекта.

Предоставляет:
- :class:`MetricsCounter` — счётчики вызовов (OData-запросы, AI-вызовы, ошибки)
- :class:`MetricsTimer` — замеры времени выполнения
- :class:`AIUsageTracker` — трекинг токенов и стоимости AI-запросов
- :class:`CostLogger` — запись каждой AI-траты в отдельный JSONL-файл
- :class:`CostAnalyzer` — агрегация затрат по интервалам (минута / час / день / …)

Все данные хранятся в памяти. Для сброса (например, между тестами)
используйте :func:`reset_metrics`.

Использование::

    from bot.metrics import metrics, track_time

    # Счётчик
    metrics.increment("odata_requests")
    metrics.increment("odata_errors", tags={"status": 500})

    # Таймер (контекстный менеджер)
    async with track_time("odata_get_entities"):
        data = await client.get_entities(...)

    # AI Usage — автоматически запишется в logs/costs/
    metrics.track_ai_usage(model="gpt-4o-mini", input_tokens=100, output_tokens=50)
    report = metrics.report()

    # Анализ затрат по дням:
    from bot.metrics import CostAnalyzer
    analyzer = CostAnalyzer("logs/costs")
    daily = analyzer.aggregate(interval="day")
    for bucket in daily:
        print(bucket)
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes for metrics storage
# ---------------------------------------------------------------------------


@dataclass
class CounterEntry:
    """Запись счётчика."""

    count: int = 0

    def increment(self, n: int = 1) -> None:
        self.count += n


@dataclass
class TimerEntry:
    """Запись таймера."""

    count: int = 0
    total_time: float = 0.0
    min_time: float = float("inf")
    max_time: float = 0.0

    def record(self, duration: float) -> None:
        self.count += 1
        self.total_time += duration
        self.min_time = min(self.min_time, duration)
        self.max_time = max(self.max_time, duration)

    @property
    def avg_time(self) -> float:
        if self.count == 0:
            return 0.0
        return self.total_time / self.count


@dataclass
class AIUsageEntry:
    """Запись использования AI по модели."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_usd: float = 0.0
    total_cost_rub: float = 0.0

    def record(
        self,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        cost_rub: float = 0.0,
    ) -> None:
        self.requests += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.total_cost_usd += cost_usd
        self.total_cost_rub += cost_rub


# ---------------------------------------------------------------------------
# Main MetricsRegistry
# ---------------------------------------------------------------------------


class MetricsRegistry:
    """Центральный реестр метрик приложения.

    Потокобезопасность: не гарантируется — предполагается использование
    в рамках одного event loop (asyncio).
    """

    def __init__(self) -> None:
        self._counters: dict[str, CounterEntry] = defaultdict(CounterEntry)
        self._timers: dict[str, TimerEntry] = defaultdict(TimerEntry)
        self._ai_usage: dict[str, AIUsageEntry] = defaultdict(AIUsageEntry)
        self._start_time: float = time.monotonic()

    # -- Counters --

    def increment(self, name: str, value: int = 1) -> None:
        """Увеличить счётчик."""
        self._counters[name].increment(value)

    def get_counter(self, name: str) -> int:
        """Получить значение счётчика."""
        return self._counters[name].count

    # -- Timers --

    def record_timer(self, name: str, duration: float) -> None:
        """Записать замер времени."""
        self._timers[name].record(duration)
        log.debug(
            "timer_recorded metric=%s duration=%.3fs",
            name,
            duration,
        )

    def get_timer(self, name: str) -> TimerEntry | None:
        """Получить запись таймера."""
        return self._timers.get(name)

    # -- AI Usage --

    def track_ai_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        input_price_per_1m: float = 0.15,
        output_price_per_1m: float = 0.60,
        cost_rub: float | None = None,
    ) -> None:
        """Записать использование AI (токены + стоимость).

        Args:
            model: название модели (например ``gpt-4o-mini``).
            input_tokens: количество входных токенов.
            output_tokens: количество выходных токенов.
            input_price_per_1m: цена за 1M входных токенов (USD).
            output_price_per_1m: цена за 1M выходных токенов (USD).
            cost_rub: стоимость запроса в рублях из ответа API (если доступна).
        """
        cost = (input_tokens * input_price_per_1m + output_tokens * output_price_per_1m) / 1_000_000
        cost_rub_val = cost_rub if cost_rub is not None else 0.0

        self._ai_usage[model].record(input_tokens, output_tokens, cost, cost_rub=cost_rub_val)

        # Персистентная запись в JSONL (если CostLogger инициализирован)
        if _cost_logger is not None:
            _cost_logger.log(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                cost_rub=cost_rub_val,
                input_price_per_1m=input_price_per_1m,
                output_price_per_1m=output_price_per_1m,
            )

        log.info(
            "ai_usage model=%s input_tokens=%d output_tokens=%d cost=$%.6f cost_rub=%.6f",
            model,
            input_tokens,
            output_tokens,
            cost,
            cost_rub_val,
        )

    def get_ai_usage(self, model: str | None = None) -> dict[str, AIUsageEntry] | AIUsageEntry | None:
        """Получить запись использования AI.

        Args:
            model: если указана — вернуть данные по конкретной модели,
                   иначе — словарь по всем моделям.
        """
        if model:
            return self._ai_usage.get(model)
        return dict(self._ai_usage)

    # -- Report --

    def report(self) -> dict[str, Any]:
        """Сформировать отчёт по всем метрикам.

        Returns:
            Словарь с ключами ``uptime_seconds``, ``counters``,
            ``timers``, ``ai_usage``.
        """
        uptime = time.monotonic() - self._start_time

        counters_report = {
            name: entry.count for name, entry in sorted(self._counters.items())
        }

        timers_report: dict[str, dict[str, Any]] = {}
        for name, entry in sorted(self._timers.items()):
            timers_report[name] = {
                "count": entry.count,
                "total_s": round(entry.total_time, 3),
                "avg_s": round(entry.avg_time, 3),
                "min_s": round(entry.min_time, 3) if entry.min_time != float("inf") else 0,
                "max_s": round(entry.max_time, 3),
            }

        ai_report: dict[str, dict[str, Any]] = {}
        total_input = 0
        total_output = 0
        total_cost = 0.0
        total_cost_rub = 0.0
        total_requests = 0
        for model, entry in sorted(self._ai_usage.items()):
            ai_report[model] = {
                "requests": entry.requests,
                "input_tokens": entry.input_tokens,
                "output_tokens": entry.output_tokens,
                "cost_usd": round(entry.total_cost_usd, 6),
                "cost_rub": round(entry.total_cost_rub, 6),
            }
            total_input += entry.input_tokens
            total_output += entry.output_tokens
            total_cost += entry.total_cost_usd
            total_cost_rub += entry.total_cost_rub
            total_requests += entry.requests

        return {
            "uptime_seconds": round(uptime, 1),
            "counters": counters_report,
            "timers": timers_report,
            "ai_usage": ai_report,
            "ai_total": {
                "requests": total_requests,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "cost_usd": round(total_cost, 6),
                "cost_rub": round(total_cost_rub, 6),
            },
        }

    def format_report(self) -> str:
        """Сформировать человекочитаемый отчёт.

        Returns:
            Строка с отформатированными метриками.
        """
        r = self.report()
        lines: list[str] = []

        # Uptime
        uptime_h = int(r["uptime_seconds"] // 3600)
        uptime_m = int((r["uptime_seconds"] % 3600) // 60)
        lines.append(f"⏱ Uptime: {uptime_h}ч {uptime_m}мин")

        # Counters
        if r["counters"]:
            lines.append("")
            lines.append("📊 Счётчики:")
            for name, count in r["counters"].items():
                lines.append(f"  • {name}: {count}")

        # Timers
        if r["timers"]:
            lines.append("")
            lines.append("⏱ Таймеры:")
            for name, t in r["timers"].items():
                lines.append(
                    f"  • {name}: {t['count']} вызовов, "
                    f"avg={t['avg_s']}с, total={t['total_s']}с"
                )

        # AI Usage
        ai = r["ai_usage"]
        ai_total = r["ai_total"]
        if ai:
            lines.append("")
            lines.append("🤖 AI использование:")
            for model, usage in ai.items():
                rub_str = f", ₽{usage['cost_rub']:.4f}" if usage.get("cost_rub") else ""
                lines.append(
                    f"  • {model}: {usage['requests']} запросов, "
                    f"IN={usage['input_tokens']}, OUT={usage['output_tokens']}, "
                    f"${usage['cost_usd']:.4f}{rub_str}"
                )
            rub_total_str = f", ₽{ai_total['cost_rub']:.4f}" if ai_total.get("cost_rub") else ""
            lines.append(
                f"  ─ Итого: {ai_total['requests']} запросов, "
                f"IN={ai_total['input_tokens']}, OUT={ai_total['output_tokens']}, "
                f"${ai_total['cost_usd']:.4f}{rub_total_str}"
            )

        return "\n".join(lines)

    def reset(self) -> None:
        """Сбросить все метрики (полезно для тестов)."""
        self._counters.clear()
        self._timers.clear()
        self._ai_usage.clear()
        self._start_time = time.monotonic()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

metrics = MetricsRegistry()


def reset_metrics() -> None:
    """Сбросить глобальный реестр метрик."""
    metrics.reset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def track_time(name: str) -> AsyncIterator[None]:
    """Асинхронный контекстный менеджер для замера времени.

    Автоматически записывает результат в :data:`metrics`.

    Usage::

        async with track_time("odata_get_entities"):
            data = await client.get_entities(...)

        # Аналогично для синхронного кода:
        with track_time("parse_metadata"):
            parser.parse(xml)
    """
    start = time.monotonic()
    try:
        yield
    finally:
        duration = time.monotonic() - start
        metrics.record_timer(name, duration)


def track_sync(name: str):
    """Декоратор для синхронных функций — замер времени выполнения.

    Usage::

        @track_sync("parse_metadata")
        def parse(xml):
            ...
    """

    def decorator(fn):
        import functools

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            start = time.monotonic()
            try:
                return fn(*args, **kwargs)
            finally:
                metrics.record_timer(name, time.monotonic() - start)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# CostLogger — персистентное логирование AI-затрат в JSONL
# ---------------------------------------------------------------------------


class CostLogger:
    """Запись каждой AI-траты в JSONL-файл с таймстемпом.

    Файлы создаются по одному на календарный день:
    ``<cost_dir>/costs_YYYY-MM-DD.jsonl``

    Каждая строка — JSON-объект::

        {
          "ts": "2026-05-04T01:25:00.123456+04:00",
          "model": "gpt-4o-mini",
          "input_tokens": 100,
          "output_tokens": 50,
          "cost_usd": 0.000045,
          "input_price_per_1m": 0.15,
          "output_price_per_1m": 0.60,
          "chat_id": 123456
        }

    Использование::

        cost_logger = CostLogger("logs/costs")
        cost_logger.log(model="gpt-4o-mini", input_tokens=100, output_tokens=50,
                        cost_usd=0.000045, chat_id=123456)
    """

    def __init__(self, cost_dir: str = "logs/costs") -> None:
        self._cost_dir = Path(cost_dir)
        self._cost_dir.mkdir(parents=True, exist_ok=True)

    def _day_filename(self, dt: datetime) -> Path:
        """Получить имя файла для указанной даты."""
        return self._cost_dir / f"costs_{dt.strftime('%Y-%m-%d')}.jsonl"

    def log(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        cost_rub: float = 0.0,
        input_price_per_1m: float = 0.0,
        output_price_per_1m: float = 0.0,
        chat_id: int | None = None,
        *,
        ts: datetime | None = None,
    ) -> None:
        """Записать одну AI-трату в JSONL-файл.

        Args:
            model: название модели.
            input_tokens: количество входных токенов.
            output_tokens: количество выходных токенов.
            cost_usd: стоимость в USD.
            cost_rub: стоимость в рублях (из ответа API провайдера).
            input_price_per_1m: цена за 1M входных (для аналитики).
            output_price_per_1m: цена за 1M выходных (для аналитики).
            chat_id: ID чата (для аналитики по чатам).
            ts: точное время события (None = сейчас).
        """
        if ts is None:
            ts = datetime.now(tz=UTC)

        entry = {
            "ts": ts.isoformat(),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost_usd, 8),
            "cost_rub": round(cost_rub, 8),
            "input_price_per_1m": input_price_per_1m,
            "output_price_per_1m": output_price_per_1m,
        }
        if chat_id is not None:
            entry["chat_id"] = chat_id

        filepath = self._day_filename(ts)
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("Failed to write cost log to %s: %s", filepath, exc)

    def read_all(self) -> list[dict[str, Any]]:
        """Прочитать все записи из всех JSONL-файлов в директории.

        Returns:
            Список словарей с записями, отсортированных по времени.
        """
        records: list[dict[str, Any]] = []
        pattern = str(self._cost_dir / "costs_*.jsonl")
        import glob as _glob

        for filepath in sorted(_glob.glob(pattern)):
            try:
                with open(filepath, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue

        return records


# ---------------------------------------------------------------------------
# CostAnalyzer — агрегация затрат по интервалам
# ---------------------------------------------------------------------------

# Поддерживаемые интервалы и их длительность в секундах
INTERVAL_SECONDS: dict[str, int] = {
    "minute": 60,
    "5min": 300,
    "15min": 900,
    "30min": 1800,
    "hour": 3600,
    "6h": 21600,
    "12h": 43200,
    "day": 86400,
    "week": 604800,
    "month": 2592000,  # ~30 дней
}


@dataclass
class CostBucket:
    """Агрегированная запись затрат за интервал."""

    interval: str          # "minute", "hour", "day", …
    bucket_start: str      # ISO timestamp начала интервала
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    models: dict[str, int] = field(default_factory=dict)  # model → requests

    def __str__(self) -> str:
        cost = self.cost_usd
        # Красивое форматирование стоимости
        if cost < 0.01:
            cost_str = f"${cost:.6f}"
        elif cost < 1.0:
            cost_str = f"${cost:.4f}"
        else:
            cost_str = f"${cost:.2f}"

        models_str = ", ".join(f"{m}({c})" for m, c in sorted(self.models.items()))
        return (
            f"[{self.bucket_start}] {self.interval}: "
            f"{self.requests} req, "
            f"IN={self.input_tokens}, OUT={self.output_tokens}, "
            f"{cost_str}"
            f"{f' ({models_str})' if models_str else ''}"
        )


class CostAnalyzer:
    """Агрегация затрат из JSONL-логов по временным интервалам.

    Использование::

        analyzer = CostAnalyzer("logs/costs")

        # Затраты по дням:
        for bucket in analyzer.aggregate("day"):
            print(bucket)

        # Затраты по часам за конкретную дату:
        from datetime import datetime, timezone
        buckets = analyzer.aggregate(
            "hour",
            since=datetime(2026, 5, 1, tzinfo=timezone.utc),
            until=datetime(2026, 5, 4, tzinfo=timezone.utc),
        )

        # Сводка:
        summary = analyzer.summary("day")
        print(summary)
    """

    def __init__(self, cost_dir: str = "logs/costs") -> None:
        self._cost_dir = cost_dir

    def _parse_ts(self, ts_str: str) -> datetime:
        """Разобрать ISO timestamp из JSONL."""
        return datetime.fromisoformat(ts_str)

    def _bucket_start(self, dt: datetime, interval: str) -> datetime:
        """Вычислить начало интервала для заданного datetime."""
        interval_sec = INTERVAL_SECONDS.get(interval, 86400)

        if interval == "month":
            return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if interval == "week":
            # Начало недели (понедельник)
            days_since_monday = dt.weekday()
            return (dt - __import__("datetime").timedelta(days=days_since_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        if interval == "day":
            return dt.replace(hour=0, minute=0, second=0, microsecond=0)
        if interval in ("12h", "6h"):
            hour_offset = (dt.hour // (interval_sec // 3600)) * (interval_sec // 3600)
            return dt.replace(hour=hour_offset, minute=0, second=0, microsecond=0)
        if interval == "hour":
            return dt.replace(minute=0, second=0, microsecond=0)
        if interval in ("30min", "15min", "5min", "minute"):
            minutes_per_bucket = interval_sec // 60
            minute_offset = (dt.minute // minutes_per_bucket) * minutes_per_bucket
            return dt.replace(minute=minute_offset, second=0, microsecond=0)

        # Fallback
        epoch = dt.timestamp()
        bucket_epoch = (epoch // interval_sec) * interval_sec
        return datetime.fromtimestamp(bucket_epoch, tz=dt.tzinfo)

    def aggregate(
        self,
        interval: str = "day",
        since: datetime | None = None,
        until: datetime | None = None,
        model: str | None = None,
    ) -> list[CostBucket]:
        """Агрегировать затраты по интервалам.

        Args:
            interval: один из ``minute``, ``5min``, ``15min``, ``30min``,
                      ``hour``, ``6h``, ``12h``, ``day``, ``week``, ``month``.
            since: начало периода (None = с самой ранней записи).
            until: конец периода (None = по самую позднюю запись).
            model: фильтр по модели (None = все модели).

        Returns:
            Список :class:`CostBucket`, отсортированных по времени.
        """
        logger = CostLogger(self._cost_dir)
        records = logger.read_all()

        # Фильтрация по времени
        if since or until:
            filtered: list[dict[str, Any]] = []
            for rec in records:
                try:
                    ts = self._parse_ts(rec["ts"])
                except (KeyError, ValueError):
                    continue
                if since and ts < since:
                    continue
                if until and ts > until:
                    continue
                filtered.append(rec)
            records = filtered

        # Фильтрация по модели
        if model:
            records = [r for r in records if r.get("model") == model]

        # Группировка по интервалам
        buckets: dict[str, CostBucket] = {}
        for rec in records:
            try:
                ts = self._parse_ts(rec["ts"])
            except (KeyError, ValueError):
                continue

            bucket_dt = self._bucket_start(ts, interval)
            bucket_key = bucket_dt.isoformat()

            if bucket_key not in buckets:
                buckets[bucket_key] = CostBucket(
                    interval=interval,
                    bucket_start=bucket_key,
                )

            b = buckets[bucket_key]
            b.requests += 1
            b.input_tokens += rec.get("input_tokens", 0)
            b.output_tokens += rec.get("output_tokens", 0)
            b.cost_usd += rec.get("cost_usd", 0.0)

            mdl = rec.get("model", "unknown")
            b.models[mdl] = b.models.get(mdl, 0) + 1

        return sorted(buckets.values(), key=lambda b: b.bucket_start)

    def summary(
        self,
        interval: str = "day",
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> str:
        """Сформировать человекочитаемую сводку затрат.

        Args:
            interval: интервал агрегации.
            since: начало периода.
            until: конец периода.

        Returns:
            Отформатированная строка.
        """
        buckets = self.aggregate(interval=interval, since=since, until=until)

        if not buckets:
            return "💰 Нет данных о затратах за указанный период."

        total_cost = sum(b.cost_usd for b in buckets)
        total_req = sum(b.requests for b in buckets)
        total_in = sum(b.input_tokens for b in buckets)
        total_out = sum(b.output_tokens for b in buckets)

        interval_ru = {
            "minute": "минута", "5min": "5 минут", "15min": "15 минут",
            "30min": "30 минут", "hour": "час", "6h": "6 часов",
            "12h": "12 часов", "day": "день", "week": "неделя", "month": "месяц",
        }.get(interval, interval)

        lines: list[str] = [
            f"💰 Затраты на AI (интервал: {interval_ru}):",
            "",
        ]

        for b in buckets:
            lines.append(f"  {b}")

        lines.append("")
        cost_str = f"${total_cost:.6f}" if total_cost < 0.01 else f"${total_cost:.4f}"
        lines.append(
            f"  ═ Итого: {total_req} запросов, "
            f"IN={total_in}, OUT={total_out}, {cost_str}"
        )

        return "\n".join(lines)

    def total_cost(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        model: str | None = None,
    ) -> float:
        """Получить суммарную стоимость за период.

        Args:
            since: начало периода.
            until: конец периода.
            model: фильтр по модели.

        Returns:
            Суммарная стоимость в USD.
        """
        buckets = self.aggregate(interval="minute", since=since, until=until, model=model)
        return sum(b.cost_usd for b in buckets)


# ---------------------------------------------------------------------------
# Интеграция CostLogger в MetricsRegistry
# ---------------------------------------------------------------------------

# Глобальный CostLogger — создаётся при первом обращении
_cost_logger: CostLogger | None = None


def setup_cost_logging(cost_dir: str = "logs/costs") -> None:
    """Инициализировать логирование затрат в JSONL-файлы.

    Вызывается один раз при старте приложения (из ``__main__.py``).

    Args:
        cost_dir: папка для JSONL-файлов с затратами.
    """
    global _cost_logger
    _cost_logger = CostLogger(cost_dir)
    log.info("cost_logging_initialized cost_dir=%s", cost_dir)


def get_cost_logger() -> CostLogger | None:
    """Получить текущий CostLogger (или None, если не инициализирован)."""
    return _cost_logger


# ---------------------------------------------------------------------------
# ProviderResponseSaver — сохранение ответов провайдера AI
# ---------------------------------------------------------------------------

_provider_response_dir: str | None = None


def setup_provider_response_logging(log_dir: str = "logs") -> None:
    """Инициализировать сохранение ответов провайдера.

    Ответы сохраняются в подкаталогах ``<log_dir>/<session_id>/``,
    где ``<session_id>`` — уникальный идентификатор сессии (постоянный весь запуск).

    Args:
        log_dir: корневая папка логов.
    """
    global _provider_response_dir
    _provider_response_dir = log_dir
    log.info("provider_response_logging_initialized log_dir=%s", log_dir)


def save_provider_response(
    *,
    step: str,
    model: str,
    request_messages: list[dict[str, Any]],
    response_data: Any,
    log_stem: str | None = None,
) -> str | None:
    """Сохранить ответ провайдера AI в JSON-файл.

    Файлы сохраняются в ``logs/<session_id>/NNN_<step>.json``,
    где ``<session_id>`` — идентификатор сессии (не меняется при ротации логов),
    ``NNN`` — порядковый номер запроса в сессии.

    Args:
        step: этап (``"step1"``, ``"step2"``, ``"formatter"`` и т.д.).
        model: модель AI.
        request_messages: список сообщений запроса.
        response_data: данные ответа (сериализуемый dict).
        log_stem: явное имя папки (если None — используется ``session_id``).

    Returns:
        Путь к сохранённому файлу или None при ошибке.
    """
    from bot.logging_config import get_session_id

    if _provider_response_dir is None:
        return None

    stem = log_stem or get_session_id()
    if not stem:
        log.debug("save_provider_response skipped: no session_id")
        return None

    # Каталог для ответов этой сессии
    response_dir = Path(_provider_response_dir) / stem
    response_dir.mkdir(parents=True, exist_ok=True)

    # Определить следующий порядковый номер
    existing = list(response_dir.glob("*.json"))
    next_num = len(existing) + 1

    # Имя файла: 001_step1.json, 002_step2.json, …
    filename = f"{next_num:03d}_{step}.json"
    filepath = response_dir / filename

    # Собрать полную запись
    record = {
        "ts": datetime.now(tz=UTC).isoformat(),
        "step": step,
        "model": model,
        "request": {
            "messages_count": len(request_messages),
            "messages": request_messages,
        },
        "response": response_data,
    }

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2, default=str)
        log.info(
            "provider_response_saved file=%s step=%s model=%s",
            filepath,
            step,
            model,
        )
        return str(filepath)
    except (OSError, TypeError, ValueError) as exc:
        log.warning(
            "Failed to save provider response to %s: %s [%s]",
            filepath,
            exc,
            type(exc).__name__,
        )
        return None
