from datetime import date
from decimal import Decimal

import pytest

from core.exchange_rates import ExchangeRateConversion
from core.statistics import (
    DateRange,
    StatisticsFilters,
    StatisticsQueryScope,
    StatisticsScopeMode,
    StatisticsService,
    render_recent_expenses,
    render_spending_comparison,
    render_spending_summary,
    render_top_expenses,
    resolve_period,
)
from integrations.google_sheets.repository import TransactionRecord


def test_resolve_period_uses_backend_calendar_boundaries():
    assert resolve_period("this_month", today=date(2026, 7, 21)) == DateRange(
        "2026-07-01", "2026-07-21"
    )
    assert resolve_period("last_month", today=date(2026, 7, 21)) == DateRange(
        "2026-06-01", "2026-06-30"
    )
    assert resolve_period("this_week", today=date(2026, 7, 21)) == DateRange(
        "2026-07-20", "2026-07-21"
    )


def test_resolve_period_rejects_unbounded_custom_range():
    with pytest.raises(ValueError, match="366 days"):
        resolve_period(
            "custom",
            today=date(2026, 7, 21),
            start_date="2025-01-01",
            end_date="2026-07-21",
        )


def test_statistics_summary_filters_and_converts_without_mutating_repository():
    repository = FakeStatisticsRepository(
        records=[
            make_record("food-sgd", amount="10", category="餐饮"),
            make_record(
                "food-cny",
                amount="30",
                currency="CNY",
                category="餐饮",
            ),
            make_record("taxi", amount="9", category="交通"),
        ]
    )
    rates = FakeExchangeRateProvider()
    service = StatisticsService(
        repository=repository,
        exchange_rate_provider=rates,
        currency="SGD",
    )

    summary = service.get_spending_summary(
        scope=StatisticsQueryScope(
            mode=StatisticsScopeMode.CONVERSATION,
            source_platform="telegram",
            source_user_id="42",
            source_chat_id="group-1",
        ),
        date_range=DateRange("2026-07-01", "2026-07-21"),
        filters=StatisticsFilters(category="餐饮"),
    )

    assert summary.total == Decimal("15.40")
    assert summary.record_count == 2
    assert summary.foreign_totals == (("CNY", Decimal("30")),)
    assert summary.category_totals == (("餐饮", Decimal("15.40")),)
    assert repository.list_calls == [
        (
            StatisticsScopeMode.CONVERSATION,
            "telegram",
            "42",
            "group-1",
            "2026-07-01",
            "2026-07-21",
        )
    ]
    assert repository.mutation_calls == []


def test_statistics_compare_returns_deterministic_difference_and_percentage():
    repository = FakeStatisticsRepository(
        records_by_range={
            ("2026-07-01", "2026-07-21"): [make_record("current", amount="150")],
            ("2026-06-01", "2026-06-21"): [make_record("previous", amount="100")],
        }
    )
    service = StatisticsService(repository=repository, currency="SGD")

    comparison = service.compare_spending_periods(
        scope=personal_scope(),
        current_range=DateRange("2026-07-01", "2026-07-21"),
        comparison_range=DateRange("2026-06-01", "2026-06-21"),
    )

    assert comparison.difference == Decimal("50")
    assert comparison.percentage_change == Decimal("50.00")
    assert render_spending_comparison(comparison) == (
        "2026-07-01 至 2026-07-21 支出：150.00 SGD\n"
        "对比 2026-06-01 至 2026-06-21：100.00 SGD\n"
        "增加：50.00 SGD（50.00%）"
    )


def test_statistics_top_and_recent_expenses_use_deterministic_order():
    records = [
        make_record("small", amount="5", date_value="2026-07-21"),
        make_record("large", amount="20", date_value="2026-07-20"),
        make_record("middle", amount="10", date_value="2026-07-19"),
    ]
    repository = FakeStatisticsRepository(records=records, recent_records=records)
    service = StatisticsService(repository=repository, currency="SGD")

    top = service.get_top_expenses(
        scope=personal_scope(),
        date_range=DateRange("2026-07-01", "2026-07-21"),
        limit=2,
    )
    recent = service.list_recent_expenses(
        scope=personal_scope(),
        limit=2,
    )

    assert [expense.record.id for expense in top.expenses] == ["large", "middle"]
    assert [record.id for record in recent.records] == ["small", "large"]
    assert repository.recent_calls == [("telegram", "42", None, None, 2)]
    assert render_top_expenses(top) == (
        "2026-07-01 至 2026-07-21 最高支出：\n"
        "1. 2026-07-20 餐饮 20 SGD\n"
        "2. 2026-07-19 餐饮 10 SGD"
    )
    assert render_recent_expenses(recent) == (
        "最近支出：\n"
        "1. 2026-07-21 餐饮 5 SGD\n"
        "2. 2026-07-20 餐饮 20 SGD"
    )


def test_statistics_summary_renderer_uses_only_calculated_result():
    repository = FakeStatisticsRepository(
        records=[
            make_record("food", amount="60", category="餐饮"),
            make_record("taxi", amount="40", category="交通"),
        ]
    )
    service = StatisticsService(repository=repository, currency="SGD")

    summary = service.get_spending_summary(
        scope=personal_scope(),
        date_range=DateRange("2026-07-01", "2026-07-21"),
    )

    assert render_spending_summary(summary) == (
        "2026-07-01 至 2026-07-21 支出合计：100.00 SGD\n"
        "分类占比：\n"
        "- 餐饮：60.00 SGD（60.00%）\n"
        "- 交通：40.00 SGD（40.00%）"
    )


class FakeStatisticsRepository:
    def __init__(
        self,
        *,
        records: list[TransactionRecord] | None = None,
        records_by_range: dict[tuple[str, str], list[TransactionRecord]] | None = None,
        recent_records: list[TransactionRecord] | None = None,
    ) -> None:
        self._records = records or []
        self._records_by_range = records_by_range or {}
        self._recent_records = recent_records if recent_records is not None else []
        self.list_calls: list[tuple[str, str, str, str]] = []
        self.recent_calls: list[
            tuple[str, str, str | None, str | None, int]
        ] = []
        self.mutation_calls: list[object] = []

    def list_expenses(
        self,
        *,
        scope: StatisticsQueryScope,
        start_date: str,
        end_date: str,
    ) -> list[TransactionRecord]:
        self.list_calls.append(
            (
                scope.mode,
                scope.source_platform,
                scope.source_user_id,
                scope.source_chat_id,
                start_date,
                end_date,
            )
        )
        return list(self._records_by_range.get((start_date, end_date), self._records))

    def list_recent_expenses(
        self,
        *,
        scope: StatisticsQueryScope,
        category: str | None,
        merchant: str | None,
        limit: int,
    ) -> list[TransactionRecord]:
        self.recent_calls.append(
            (
                scope.source_platform,
                scope.source_user_id,
                category,
                merchant,
                limit,
            )
        )
        return list(self._recent_records[:limit])


class FakeExchangeRateProvider:
    def convert(
        self,
        amount: Decimal,
        *,
        from_currency: str,
        to_currency: str,
        date: str,
    ) -> ExchangeRateConversion:
        assert (from_currency, to_currency) == ("CNY", "SGD")
        return ExchangeRateConversion(
            original_amount=amount,
            original_currency=from_currency,
            converted_amount=amount * Decimal("0.18"),
            converted_currency=to_currency,
            rate=Decimal("0.18"),
            rate_date=date,
        )


def personal_scope() -> StatisticsQueryScope:
    return StatisticsQueryScope(
        mode=StatisticsScopeMode.PERSONAL,
        source_platform="telegram",
        source_user_id="42",
        source_chat_id="42",
    )


def make_record(
    transaction_id: str,
    *,
    amount: str,
    currency: str = "SGD",
    category: str = "餐饮",
    date_value: str = "2026-07-20",
) -> TransactionRecord:
    return TransactionRecord(
        id=transaction_id,
        date=date_value,
        amount=Decimal(amount),
        currency=currency,
        type="expense",
        category=category,
        merchant=None,
        payment_method=None,
        note=None,
        source_platform="telegram",
        source_user_id="42",
        source_username=None,
        source_user_display_name=None,
        source_chat_id="chat",
        source_message_id=transaction_id,
        created_at=f"{date_value}T12:00:00+08:00",
        updated_at=f"{date_value}T12:00:00+08:00",
    )
