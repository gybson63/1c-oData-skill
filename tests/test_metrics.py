#!/usr/bin/env python3
"""Тесты модуля метрик (bot.metrics)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bot.metrics import (
    CostAnalyzer,
    CostBucket,
    CostLogger,
    MetricsRegistry,
    SessionTokens,
    SessionTokenTracker,
    get_cost_logger,
    metrics,
    reset_metrics,
    setup_cost_logging,
    track_sync,
    track_time,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry():
    """Свежий реестр метрик для каждого теста."""
    return MetricsRegistry()


# ---------------------------------------------------------------------------
# Counter tests
# ---------------------------------------------------------------------------


class TestCounters:
    def test_increment_new_counter(self, registry):
        registry.increment("test_counter")
        assert registry.get_counter("test_counter") == 1

    def test_increment_multiple(self, registry):
        registry.increment("test_counter", 5)
        assert registry.get_counter("test_counter") == 5

    def test_increment_accumulates(self, registry):
        registry.increment("test_counter")
        registry.increment("test_counter")
        registry.increment("test_counter")
        assert registry.get_counter("test_counter") == 3

    def test_get_counter_default_zero(self, registry):
        assert registry.get_counter("nonexistent") == 0

    def test_multiple_counters(self, registry):
        registry.increment("counter_a")
        registry.increment("counter_b", 3)
        registry.increment("counter_a", 2)
        assert registry.get_counter("counter_a") == 3
        assert registry.get_counter("counter_b") == 3


# ---------------------------------------------------------------------------
# Timer tests
# ---------------------------------------------------------------------------


class TestTimers:
    def test_record_timer(self, registry):
        registry.record_timer("test_timer", 0.5)
        entry = registry.get_timer("test_timer")
        assert entry is not None
        assert entry.count == 1
        assert entry.total_time == pytest.approx(0.5, abs=0.001)
        assert entry.min_time == pytest.approx(0.5, abs=0.001)
        assert entry.max_time == pytest.approx(0.5, abs=0.001)

    def test_record_timer_multiple(self, registry):
        registry.record_timer("test_timer", 0.1)
        registry.record_timer("test_timer", 0.3)
        entry = registry.get_timer("test_timer")
        assert entry.count == 2
        assert entry.total_time == pytest.approx(0.4, abs=0.001)
        assert entry.min_time == pytest.approx(0.1, abs=0.001)
        assert entry.max_time == pytest.approx(0.3, abs=0.001)
        assert entry.avg_time == pytest.approx(0.2, abs=0.001)

    def test_get_timer_nonexistent(self, registry):
        assert registry.get_timer("nonexistent") is None

    def test_avg_time_zero_count(self):
        from bot.metrics import TimerEntry
        entry = TimerEntry()
        assert entry.avg_time == 0.0


# ---------------------------------------------------------------------------
# AI Usage tests
# ---------------------------------------------------------------------------


class TestAIUsage:
    def test_track_ai_usage(self, registry):
        registry.track_ai_usage(
            model="gpt-4o-mini",
            input_tokens=1000,
            output_tokens=500,
            input_price_per_1m=0.15,
            output_price_per_1m=0.60,
        )
        entry = registry.get_ai_usage("gpt-4o-mini")
        assert entry is not None
        assert entry.requests == 1
        assert entry.input_tokens == 1000
        assert entry.output_tokens == 500
        # cost = (1000 * 0.15 + 500 * 0.60) / 1_000_000 = 0.00045
        assert entry.total_cost_usd == pytest.approx(0.00045, abs=0.000001)
        assert entry.total_cost_rub == 0.0  # cost_rub не передан

    def test_track_ai_usage_with_cost_rub(self, registry):
        registry.track_ai_usage(
            model="gpt-4o-mini",
            input_tokens=1000,
            output_tokens=500,
            input_price_per_1m=0.15,
            output_price_per_1m=0.60,
            cost_rub=0.13122624,
        )
        entry = registry.get_ai_usage("gpt-4o-mini")
        assert entry is not None
        assert entry.total_cost_rub == pytest.approx(0.13122624, abs=0.000001)

    def test_track_ai_usage_accumulates(self, registry):
        registry.track_ai_usage("gpt-4o", 100, 50, 0.15, 0.60)
        registry.track_ai_usage("gpt-4o", 200, 100, 0.15, 0.60, cost_rub=0.05)
        entry = registry.get_ai_usage("gpt-4o")
        assert entry.requests == 2
        assert entry.input_tokens == 300
        assert entry.output_tokens == 150
        assert entry.total_cost_rub == pytest.approx(0.05, abs=0.000001)

    def test_track_ai_usage_multiple_models(self, registry):
        registry.track_ai_usage("model-a", 100, 50, 0.15, 0.60)
        registry.track_ai_usage("model-b", 200, 100, 0.15, 0.60)
        all_usage = registry.get_ai_usage()
        assert isinstance(all_usage, dict)
        assert "model-a" in all_usage
        assert "model-b" in all_usage

    def test_get_ai_usage_nonexistent(self, registry):
        entry = registry.get_ai_usage("nonexistent")
        assert entry is None


# ---------------------------------------------------------------------------
# Report tests
# ---------------------------------------------------------------------------


class TestReport:
    def test_report_structure(self, registry):
        registry.increment("test_counter")
        registry.record_timer("test_timer", 0.1)
        registry.track_ai_usage("gpt-4o-mini", 100, 50, 0.15, 0.60)

        report = registry.report()
        assert "uptime_seconds" in report
        assert "counters" in report
        assert "timers" in report
        assert "ai_usage" in report
        assert "ai_total" in report
        assert report["counters"]["test_counter"] == 1
        assert "test_timer" in report["timers"]
        assert "gpt-4o-mini" in report["ai_usage"]

    def test_report_ai_total(self, registry):
        registry.track_ai_usage("model-a", 100, 50, 0.15, 0.60, cost_rub=0.10)
        registry.track_ai_usage("model-b", 200, 100, 0.15, 0.60, cost_rub=0.20)

        report = registry.report()
        total = report["ai_total"]
        assert total["requests"] == 2
        assert total["input_tokens"] == 300
        assert total["output_tokens"] == 150
        assert total["cost_rub"] == pytest.approx(0.30, abs=0.001)

    def test_report_includes_cost_rub_per_model(self, registry):
        registry.track_ai_usage("gpt-4o-mini", 100, 50, 0.15, 0.60, cost_rub=0.13122624)

        report = registry.report()
        usage = report["ai_usage"]["gpt-4o-mini"]
        assert "cost_rub" in usage
        assert usage["cost_rub"] == pytest.approx(0.131226, abs=0.0001)

    def test_format_report(self, registry):
        registry.increment("odata_requests")
        registry.record_timer("odata_get", 0.5)
        registry.track_ai_usage("gpt-4o-mini", 100, 50, 0.15, 0.60)

        text = registry.format_report()
        assert "Uptime:" in text
        assert "odata_requests" in text
        assert "odata_get" in text
        assert "gpt-4o-mini" in text

    def test_format_report_with_cost_rub(self, registry):
        registry.track_ai_usage("gpt-4o-mini", 100, 50, 0.15, 0.60, cost_rub=0.1312)

        text = registry.format_report()
        assert "₽0.1312" in text


# ---------------------------------------------------------------------------
# Reset tests
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_clears_all(self, registry):
        registry.increment("counter")
        registry.record_timer("timer", 0.1)
        registry.track_ai_usage("model", 100, 50, 0.15, 0.60)

        registry.reset()
        assert registry.get_counter("counter") == 0
        assert registry.get_timer("timer") is None
        assert registry.get_ai_usage("model") is None

    def test_reset_metrics_global(self):
        metrics.increment("test_global")
        reset_metrics()
        assert metrics.get_counter("test_global") == 0


# ---------------------------------------------------------------------------
# Context manager / decorator tests
# ---------------------------------------------------------------------------


class TestTrackTime:
    @pytest.mark.asyncio
    async def test_track_time_async(self, registry):
        import asyncio
        # Use the global metrics singleton (reset first)
        reset_metrics()
        async with track_time("test_async"):
            await asyncio.sleep(0.01)  # ensure measurable duration

        entry = metrics.get_timer("test_async")
        assert entry is not None
        assert entry.count == 1
        assert entry.total_time > 0

    def test_track_sync_decorator(self):
        reset_metrics()

        @track_sync("test_sync_fn")
        def dummy():
            return 42

        result = dummy()
        assert result == 42

        entry = metrics.get_timer("test_sync_fn")
        assert entry is not None
        assert entry.count == 1


async def _async_dummy():
    """Helper для async-теста."""
    pass


# ---------------------------------------------------------------------------
# CostLogger tests
# ---------------------------------------------------------------------------


class TestCostLogger:
    def test_log_creates_jsonl_file(self, tmp_path):
        cost_dir = tmp_path / "costs"
        logger = CostLogger(str(cost_dir))

        logger.log(
            model="gpt-4o-mini",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.000045,
            ts=datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC),
        )

        # Check file exists
        files = list(cost_dir.glob("costs_*.jsonl"))
        assert len(files) == 1
        assert "2026-05-04" in files[0].name

        # Check content
        lines = files[0].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["model"] == "gpt-4o-mini"
        assert entry["input_tokens"] == 100
        assert entry["output_tokens"] == 50
        assert entry["cost_usd"] == pytest.approx(0.000045, abs=1e-8)
        assert entry["cost_rub"] == 0.0  # cost_rub по умолчанию

    def test_log_with_cost_rub(self, tmp_path):
        cost_dir = tmp_path / "costs"
        logger = CostLogger(str(cost_dir))

        logger.log(
            model="gpt-4o-mini",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.000045,
            cost_rub=0.13122624,
            ts=datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC),
        )

        files = list(cost_dir.glob("costs_*.jsonl"))
        entry = json.loads(files[0].read_text(encoding="utf-8").strip())
        assert entry["cost_rub"] == pytest.approx(0.13122624, abs=1e-6)

    def test_log_with_chat_id(self, tmp_path):
        cost_dir = tmp_path / "costs"
        logger = CostLogger(str(cost_dir))

        logger.log(
            model="gpt-4o-mini",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.000045,
            chat_id=123456,
            ts=datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC),
        )

        files = list(cost_dir.glob("costs_*.jsonl"))
        entry = json.loads(files[0].read_text(encoding="utf-8").strip())
        assert entry["chat_id"] == 123456

    def test_log_multiple_entries(self, tmp_path):
        cost_dir = tmp_path / "costs"
        logger = CostLogger(str(cost_dir))

        base_ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
        for i in range(5):
            logger.log(
                model="gpt-4o-mini",
                input_tokens=100 * (i + 1),
                output_tokens=50 * (i + 1),
                cost_usd=0.000045 * (i + 1),
                ts=base_ts + timedelta(minutes=i),
            )

        files = list(cost_dir.glob("costs_*.jsonl"))
        lines = files[0].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 5

    def test_log_separates_days(self, tmp_path):
        cost_dir = tmp_path / "costs"
        logger = CostLogger(str(cost_dir))

        logger.log(
            model="gpt-4o-mini", input_tokens=100, output_tokens=50,
            cost_usd=0.000045,
            ts=datetime(2026, 5, 3, 23, 59, 0, tzinfo=UTC),
        )
        logger.log(
            model="gpt-4o-mini", input_tokens=100, output_tokens=50,
            cost_usd=0.000045,
            ts=datetime(2026, 5, 4, 0, 1, 0, tzinfo=UTC),
        )

        files = sorted(cost_dir.glob("costs_*.jsonl"))
        assert len(files) == 2

    def test_read_all(self, tmp_path):
        cost_dir = tmp_path / "costs"
        logger = CostLogger(str(cost_dir))

        base_ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
        logger.log(model="gpt-4o-mini", input_tokens=100, output_tokens=50,
                    cost_usd=0.000045, ts=base_ts)
        logger.log(model="gpt-4o", input_tokens=200, output_tokens=100,
                    cost_usd=0.00009, ts=base_ts + timedelta(hours=1))

        records = logger.read_all()
        assert len(records) == 2
        assert records[0]["model"] == "gpt-4o-mini"
        assert records[1]["model"] == "gpt-4o"

    def test_read_all_empty_dir(self, tmp_path):
        logger = CostLogger(str(tmp_path / "costs"))
        records = logger.read_all()
        assert records == []


# ---------------------------------------------------------------------------
# CostAnalyzer tests
# ---------------------------------------------------------------------------


class TestCostAnalyzer:
    def _make_logger_with_data(self, tmp_path):
        """Создать CostLogger с тестовыми данными."""
        cost_dir = tmp_path / "costs"
        logger = CostLogger(str(cost_dir))

        base = datetime(2026, 5, 4, 0, 0, 0, tzinfo=UTC)
        # 3 записи в один день
        logger.log(model="gpt-4o-mini", input_tokens=100, output_tokens=50,
                    cost_usd=0.000045, ts=base + timedelta(hours=2))
        logger.log(model="gpt-4o-mini", input_tokens=200, output_tokens=100,
                    cost_usd=0.00009, ts=base + timedelta(hours=5))
        logger.log(model="gpt-4o", input_tokens=500, output_tokens=250,
                    cost_usd=0.001, ts=base + timedelta(hours=14))
        return str(cost_dir)

    def test_aggregate_by_day(self, tmp_path):
        cost_dir = self._make_logger_with_data(tmp_path)
        analyzer = CostAnalyzer(cost_dir)

        buckets = analyzer.aggregate("day")
        assert len(buckets) == 1
        assert buckets[0].requests == 3
        assert buckets[0].input_tokens == 800
        assert buckets[0].output_tokens == 400
        assert buckets[0].cost_usd == pytest.approx(0.001135, abs=0.000001)

    def test_aggregate_by_hour(self, tmp_path):
        cost_dir = self._make_logger_with_data(tmp_path)
        analyzer = CostAnalyzer(cost_dir)

        buckets = analyzer.aggregate("hour")
        assert len(buckets) == 3
        # Первый bucket — 02:00
        assert buckets[0].requests == 1
        # Второй bucket — 05:00
        assert buckets[1].requests == 1
        # Третий bucket — 14:00
        assert buckets[2].requests == 1

    def test_aggregate_with_model_filter(self, tmp_path):
        cost_dir = self._make_logger_with_data(tmp_path)
        analyzer = CostAnalyzer(cost_dir)

        buckets = analyzer.aggregate("day", model="gpt-4o-mini")
        assert len(buckets) == 1
        assert buckets[0].requests == 2

    def test_aggregate_with_time_filter(self, tmp_path):
        cost_dir = self._make_logger_with_data(tmp_path)
        analyzer = CostAnalyzer(cost_dir)

        since = datetime(2026, 5, 4, 3, 0, 0, tzinfo=UTC)
        until = datetime(2026, 5, 4, 13, 0, 0, tzinfo=UTC)

        buckets = analyzer.aggregate("hour", since=since, until=until)
        assert len(buckets) == 1  # только 05:00
        assert buckets[0].requests == 1

    def test_summary(self, tmp_path):
        cost_dir = self._make_logger_with_data(tmp_path)
        analyzer = CostAnalyzer(cost_dir)

        text = analyzer.summary("day")
        assert "Затраты" in text
        assert "Итого" in text
        assert "3 запросов" in text

    def test_total_cost(self, tmp_path):
        cost_dir = self._make_logger_with_data(tmp_path)
        analyzer = CostAnalyzer(cost_dir)

        total = analyzer.total_cost()
        assert total == pytest.approx(0.001135, abs=0.000001)

    def test_summary_empty(self, tmp_path):
        analyzer = CostAnalyzer(str(tmp_path / "empty_costs"))
        text = analyzer.summary("day")
        assert "Нет данных" in text


# ---------------------------------------------------------------------------
# CostBucket tests
# ---------------------------------------------------------------------------


class TestCostBucket:
    def test_str_format(self):
        bucket = CostBucket(
            interval="day",
            bucket_start="2026-05-04T00:00:00+00:00",
            requests=5,
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.0123,
            models={"gpt-4o-mini": 3, "gpt-4o": 2},
        )
        text = str(bucket)
        assert "day" in text
        assert "5 req" in text
        assert "gpt-4o-mini(3)" in text

    def test_str_small_cost(self):
        bucket = CostBucket(
            interval="hour",
            bucket_start="2026-05-04T12:00:00+00:00",
            cost_usd=0.000045,
        )
        text = str(bucket)
        assert "$0.000045" in text


# ---------------------------------------------------------------------------
# setup_cost_logging integration
# ---------------------------------------------------------------------------


class TestSetupCostLogging:
    def test_setup_creates_logger(self, tmp_path, monkeypatch):
        import bot.metrics as mod

        cost_dir = str(tmp_path / "costs")
        old_logger = mod._cost_logger
        try:
            mod._cost_logger = None
            setup_cost_logging(cost_dir)
            assert get_cost_logger() is not None
        finally:
            mod._cost_logger = old_logger

    def test_track_ai_usage_writes_to_cost_logger(self, tmp_path):
        import bot.metrics as mod

        cost_dir = str(tmp_path / "costs")
        old_logger = mod._cost_logger
        try:
            mod._cost_logger = CostLogger(cost_dir)
            registry = MetricsRegistry()
            registry.track_ai_usage(
                model="gpt-4o-mini",
                input_tokens=100,
                output_tokens=50,
                input_price_per_1m=0.15,
                output_price_per_1m=0.60,
            )

            # Check JSONL file was created
            files = list(Path(cost_dir).glob("costs_*.jsonl"))
            assert len(files) == 1
            entry = json.loads(files[0].read_text(encoding="utf-8").strip())
            assert entry["model"] == "gpt-4o-mini"
            assert entry["input_tokens"] == 100
        finally:
            mod._cost_logger = old_logger


# ---------------------------------------------------------------------------
# SessionTokens dataclass tests
# ---------------------------------------------------------------------------


class TestSessionTokens:
    def test_default_values(self):
        st = SessionTokens()
        assert st.input_tokens == 0
        assert st.output_tokens == 0
        assert st.requests == 0
        assert st.cost_usd == 0.0
        assert st.cost_rub == 0.0

    def test_total_tokens(self):
        st = SessionTokens(input_tokens=1000, output_tokens=500)
        assert st.total_tokens == 1500

    def test_record_with_cost(self):
        st = SessionTokens()
        st.record(input_tokens=100, output_tokens=50, cost_usd=0.000045, cost_rub=0.005)
        assert st.input_tokens == 100
        assert st.output_tokens == 50
        assert st.requests == 1
        assert st.cost_usd == pytest.approx(0.000045, abs=1e-8)
        assert st.cost_rub == pytest.approx(0.005, abs=1e-6)

    def test_record_accumulates_cost(self):
        st = SessionTokens()
        st.record(input_tokens=100, output_tokens=50, cost_usd=0.01, cost_rub=1.0)
        st.record(input_tokens=200, output_tokens=100, cost_usd=0.02, cost_rub=2.0)
        assert st.cost_usd == pytest.approx(0.03, abs=1e-6)
        assert st.cost_rub == pytest.approx(3.0, abs=1e-6)

    def test_format_compact_empty(self):
        st = SessionTokens()
        assert st.format_compact() == "📥0 📤0"

    def test_format_compact_with_tokens(self):
        st = SessionTokens(input_tokens=1000, output_tokens=500, requests=3)
        text = st.format_compact()
        assert "📥1,000" in text
        assert "📤500" in text

    def test_format_compact_with_cost_rub(self):
        st = SessionTokens(input_tokens=1000, output_tokens=500, cost_rub=1.23)
        text = st.format_compact()
        assert "💰₽1.23" in text

    def test_format_compact_with_cost_usd_only(self):
        st = SessionTokens(input_tokens=1000, output_tokens=500, cost_usd=0.05)
        text = st.format_compact()
        assert "💰$0.0500" in text

    def test_format_detail(self):
        st = SessionTokens(input_tokens=5000, output_tokens=2000, requests=10)
        text = st.format_detail()
        assert "Запросов: 10" in text
        assert "5,000" in text
        assert "2,000" in text
        assert "7,000" in text  # total

    def test_format_detail_empty(self):
        st = SessionTokens()
        text = st.format_detail()
        assert "Запросов: 0" in text

    def test_format_detail_with_cost(self):
        st = SessionTokens(input_tokens=1000, output_tokens=500, cost_usd=0.05, cost_rub=4.50)
        text = st.format_detail()
        assert "Стоимость:" in text
        assert "$0.0500" in text
        assert "₽4.50" in text

    def test_fmt_cost_small(self):
        assert SessionTokens._fmt_cost(0.000045) == "$0.000045"

    def test_fmt_cost_medium(self):
        assert SessionTokens._fmt_cost(0.05) == "$0.0500"

    def test_fmt_cost_large(self):
        assert SessionTokens._fmt_cost(5.0) == "$5.00"


# ---------------------------------------------------------------------------
# SessionTokenTracker tests
# ---------------------------------------------------------------------------


class TestSessionTokenTracker:
    @pytest.fixture
    def tracker(self):
        return SessionTokenTracker()

    def test_record_single(self, tracker):
        tracker.record(chat_id=123, input_tokens=100, output_tokens=50)
        st = tracker.get(123)
        assert st.input_tokens == 100
        assert st.output_tokens == 50
        assert st.requests == 1

    def test_record_accumulates(self, tracker):
        tracker.record(chat_id=123, input_tokens=100, output_tokens=50)
        tracker.record(chat_id=123, input_tokens=200, output_tokens=100)
        st = tracker.get(123)
        assert st.input_tokens == 300
        assert st.output_tokens == 150
        assert st.requests == 2

    def test_multiple_sessions(self, tracker):
        tracker.record(chat_id=100, input_tokens=100, output_tokens=50)
        tracker.record(chat_id=200, input_tokens=200, output_tokens=100)
        assert tracker.get(100).input_tokens == 100
        assert tracker.get(200).input_tokens == 200

    def test_get_nonexistent_session(self, tracker):
        st = tracker.get(999)
        assert st.requests == 0

    def test_clear_session(self, tracker):
        tracker.record(chat_id=123, input_tokens=100, output_tokens=50)
        tracker.clear(123)
        st = tracker.get(123)
        assert st.requests == 0
        assert st.input_tokens == 0

    def test_clear_nonexistent_session(self, tracker):
        tracker.clear(999)  # не должно падать

    def test_session_count(self, tracker):
        assert tracker.session_count == 0
        tracker.record(chat_id=100, input_tokens=100, output_tokens=50)
        assert tracker.session_count == 1
        tracker.record(chat_id=200, input_tokens=200, output_tokens=100)
        assert tracker.session_count == 2

    def test_get_compact(self, tracker):
        tracker.record(chat_id=123, input_tokens=1000, output_tokens=500)
        text = tracker.get_compact(123)
        assert "📥1,000" in text
        assert "📤500" in text

    def test_format_session_report_empty(self, tracker):
        text = tracker.format_session_report(123)
        assert "Токены текущей сессии" in text
        assert "Запросов: 0" in text

    def test_format_session_report_with_data(self, tracker):
        tracker.record(chat_id=123, input_tokens=1000, output_tokens=500)
        text = tracker.format_session_report(123)
        assert "Запросов: 1" in text
        assert "1,000" in text
        assert "500" in text

    def test_record_with_cost(self, tracker):
        tracker.record(chat_id=123, input_tokens=100, output_tokens=50,
                        cost_usd=0.000045, cost_rub=0.005)
        st = tracker.get(123)
        assert st.cost_usd == pytest.approx(0.000045, abs=1e-8)
        assert st.cost_rub == pytest.approx(0.005, abs=1e-6)

    def test_get_compact_with_cost_rub(self, tracker):
        tracker.record(chat_id=123, input_tokens=1000, output_tokens=500,
                        cost_rub=2.50)
        text = tracker.get_compact(123)
        assert "💰₽2.50" in text

    def test_format_session_report_with_cost(self, tracker):
        tracker.record(chat_id=123, input_tokens=1000, output_tokens=500,
                        cost_usd=0.05, cost_rub=4.50)
        text = tracker.format_session_report(123)
        assert "Стоимость:" in text
        assert "$0.0500" in text
        assert "₽4.50" in text
