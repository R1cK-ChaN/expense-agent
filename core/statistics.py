"""Deterministic, read-only spending statistics over authoritative records."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from core.currencies import normalize_currency_code
from core.exchange_rates import ExchangeRateProvider, ExchangeRateProviderError
from integrations.google_sheets.repository import TransactionRecord


MAX_STATISTICS_RANGE_DAYS = 366
PERCENT_QUANTUM = Decimal("0.01")


class StatisticsRepository(Protocol):
    def list_expenses(
        self,
        *,
        source_platform: str,
        user_id: str,
        start_date: str,
        end_date: str,
    ) -> list[TransactionRecord]:
        raise NotImplementedError

    def list_recent_expenses(
        self,
        *,
        source_platform: str,
        user_id: str,
        category: str | None,
        merchant: str | None,
        limit: int,
    ) -> list[TransactionRecord]:
        raise NotImplementedError


@dataclass(frozen=True)
class DateRange:
    start_date: str
    end_date: str


@dataclass(frozen=True)
class StatisticsFilters:
    category: str | None = None
    merchant: str | None = None


@dataclass(frozen=True)
class SpendingSummary:
    date_range: DateRange
    total: Decimal
    currency: str
    record_count: int
    foreign_totals: tuple[tuple[str, Decimal], ...]
    exchange_rate_dates: tuple[tuple[str, str], ...]
    category_totals: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True)
class SpendingComparison:
    current: SpendingSummary
    comparison: SpendingSummary
    difference: Decimal
    percentage_change: Decimal | None


@dataclass(frozen=True)
class RankedExpense:
    record: TransactionRecord
    converted_amount: Decimal
    currency: str
    rate_date: str | None


@dataclass(frozen=True)
class TopExpenses:
    date_range: DateRange
    expenses: tuple[RankedExpense, ...]


@dataclass(frozen=True)
class RecentExpenses:
    records: tuple[TransactionRecord, ...]


class StatisticsService:
    def __init__(
        self,
        *,
        repository: StatisticsRepository,
        currency: str,
        exchange_rate_provider: ExchangeRateProvider | None = None,
    ) -> None:
        normalized_currency = normalize_currency_code(currency)
        if normalized_currency is None:
            raise ValueError("statistics currency must be supported")
        self._repository = repository
        self._currency = normalized_currency
        self._exchange_rate_provider = exchange_rate_provider

    def get_spending_summary(
        self,
        *,
        source_platform: str,
        user_id: str,
        date_range: DateRange,
        filters: StatisticsFilters = StatisticsFilters(),
    ) -> SpendingSummary:
        _validate_range(date_range)
        records = self._repository.list_expenses(
            source_platform=source_platform,
            user_id=user_id,
            start_date=date_range.start_date,
            end_date=date_range.end_date,
        )
        return summarize_expense_records(
            _filter_records(records, filters),
            date_range=date_range,
            currency=self._currency,
            exchange_rate_provider=self._exchange_rate_provider,
        )

    def compare_spending_periods(
        self,
        *,
        source_platform: str,
        user_id: str,
        current_range: DateRange,
        comparison_range: DateRange,
        filters: StatisticsFilters = StatisticsFilters(),
    ) -> SpendingComparison:
        current = self.get_spending_summary(
            source_platform=source_platform,
            user_id=user_id,
            date_range=current_range,
            filters=filters,
        )
        comparison = self.get_spending_summary(
            source_platform=source_platform,
            user_id=user_id,
            date_range=comparison_range,
            filters=filters,
        )
        difference = current.total - comparison.total
        percentage_change = (
            None
            if comparison.total == 0
            else (difference / comparison.total * Decimal("100")).quantize(
                PERCENT_QUANTUM
            )
        )
        return SpendingComparison(
            current=current,
            comparison=comparison,
            difference=difference,
            percentage_change=percentage_change,
        )

    def get_top_expenses(
        self,
        *,
        source_platform: str,
        user_id: str,
        date_range: DateRange,
        limit: int,
        filters: StatisticsFilters = StatisticsFilters(),
    ) -> TopExpenses:
        _validate_limit(limit)
        _validate_range(date_range)
        records = self._repository.list_expenses(
            source_platform=source_platform,
            user_id=user_id,
            start_date=date_range.start_date,
            end_date=date_range.end_date,
        )
        ranked = [self._ranked_expense(record) for record in _filter_records(records, filters)]
        ranked.sort(
            key=lambda expense: (
                -expense.converted_amount,
                expense.record.date,
                expense.record.id,
            )
        )
        return TopExpenses(date_range=date_range, expenses=tuple(ranked[:limit]))

    def list_recent_expenses(
        self,
        *,
        source_platform: str,
        user_id: str,
        limit: int,
        filters: StatisticsFilters = StatisticsFilters(),
    ) -> RecentExpenses:
        _validate_limit(limit)
        records = self._repository.list_recent_expenses(
            source_platform=source_platform,
            user_id=user_id,
            category=filters.category,
            merchant=filters.merchant,
            limit=limit,
        )
        records.sort(
            key=lambda record: (record.date, record.created_at, record.id),
            reverse=True,
        )
        return RecentExpenses(records=tuple(records[:limit]))

    def _ranked_expense(self, record: TransactionRecord) -> RankedExpense:
        converted_amount, rate_date = _convert_amount(
            record,
            currency=self._currency,
            exchange_rate_provider=self._exchange_rate_provider,
        )
        return RankedExpense(
            record=record,
            converted_amount=converted_amount,
            currency=self._currency,
            rate_date=rate_date,
        )


def resolve_period(
    kind: str,
    *,
    today: date,
    start_date: str | None = None,
    end_date: str | None = None,
) -> DateRange:
    if kind == "today":
        return _date_range(today, today)
    if kind == "yesterday":
        yesterday = today - timedelta(days=1)
        return _date_range(yesterday, yesterday)
    if kind == "this_week":
        return _date_range(today - timedelta(days=today.weekday()), today)
    if kind == "last_week":
        current_week_start = today - timedelta(days=today.weekday())
        return _date_range(
            current_week_start - timedelta(days=7),
            current_week_start - timedelta(days=1),
        )
    if kind == "this_month":
        return _date_range(today.replace(day=1), today)
    if kind == "last_month":
        current_month_start = today.replace(day=1)
        previous_month_end = current_month_start - timedelta(days=1)
        previous_month_start = previous_month_end.replace(day=1)
        return _date_range(previous_month_start, previous_month_end)
    if kind != "custom" or start_date is None or end_date is None:
        raise ValueError("statistics period is invalid")
    custom_range = DateRange(start_date=start_date, end_date=end_date)
    _validate_range(custom_range)
    return custom_range


def summarize_expense_records(
    records: Sequence[TransactionRecord],
    *,
    date_range: DateRange,
    currency: str,
    exchange_rate_provider: ExchangeRateProvider | None,
) -> SpendingSummary:
    total = Decimal("0")
    foreign_totals: dict[str, Decimal] = {}
    exchange_rate_dates: set[tuple[str, str]] = set()
    category_totals: dict[str, Decimal] = {}
    for record in records:
        converted_amount, rate_date = _convert_amount(
            record,
            currency=currency,
            exchange_rate_provider=exchange_rate_provider,
        )
        record_currency = normalize_currency_code(record.currency)
        if record_currency is None:
            raise ExchangeRateProviderError(
                f"Unsupported stored currency: {record.currency}"
            )
        total += converted_amount
        category_totals[record.category] = (
            category_totals.get(record.category, Decimal("0")) + converted_amount
        )
        if record_currency != currency:
            foreign_totals[record_currency] = (
                foreign_totals.get(record_currency, Decimal("0")) + record.amount
            )
            if rate_date is not None:
                exchange_rate_dates.add((record_currency, rate_date))

    return SpendingSummary(
        date_range=date_range,
        total=total,
        currency=currency,
        record_count=len(records),
        foreign_totals=tuple(sorted(foreign_totals.items())),
        exchange_rate_dates=tuple(sorted(exchange_rate_dates)),
        category_totals=tuple(
            sorted(category_totals.items(), key=lambda item: (-item[1], item[0]))
        ),
    )


def render_spending_summary(summary: SpendingSummary) -> str:
    lines = [
        f"{_format_period(summary.date_range)} 支出合计："
        f"{_format_money(summary.total)} {summary.currency}"
    ]
    if summary.foreign_totals:
        foreign_parts = [
            f"{_format_original_money(value)} {code}"
            for code, value in summary.foreign_totals
        ]
        lines.append(
            f"外币支出（{len(summary.foreign_totals)} 种）：" + "；".join(foreign_parts)
        )
        rate_date_parts = [
            f"{code} {rate_date}" for code, rate_date in summary.exchange_rate_dates
        ]
        lines.append("汇率日：" + "；".join(rate_date_parts))
    if summary.category_totals:
        lines.append("分类占比：")
        for category, category_total in summary.category_totals:
            percentage = (
                Decimal("0")
                if summary.total == 0
                else category_total / summary.total * Decimal("100")
            )
            lines.append(
                f"- {category}：{_format_money(category_total)} {summary.currency}"
                f"（{percentage.quantize(PERCENT_QUANTUM)}%）"
            )
    return "\n".join(lines)


def render_spending_comparison(comparison: SpendingComparison) -> str:
    difference = comparison.difference
    if difference > 0:
        change = "增加"
    elif difference < 0:
        change = "减少"
    else:
        change = "持平"
    percentage = (
        "无法计算百分比"
        if comparison.percentage_change is None
        else f"{abs(comparison.percentage_change).quantize(PERCENT_QUANTUM)}%"
    )
    return "\n".join(
        [
            f"{_format_period(comparison.current.date_range)} 支出："
            f"{_format_money(comparison.current.total)} {comparison.current.currency}",
            f"对比 {_format_period(comparison.comparison.date_range)}："
            f"{_format_money(comparison.comparison.total)} "
            f"{comparison.comparison.currency}",
            f"{change}：{_format_money(abs(difference))} "
            f"{comparison.current.currency}（{percentage}）",
        ]
    )


def render_top_expenses(result: TopExpenses) -> str:
    if not result.expenses:
        return f"{_format_period(result.date_range)} 没有找到支出记录。"
    lines = [f"{_format_period(result.date_range)} 最高支出："]
    lines.extend(
        _format_ranked_expense(index, expense)
        for index, expense in enumerate(result.expenses, start=1)
    )
    return "\n".join(lines)


def render_recent_expenses(result: RecentExpenses) -> str:
    if not result.records:
        return "没有找到最近的支出记录。"
    lines = ["最近支出："]
    lines.extend(
        f"{index}. {_format_record(record)}"
        for index, record in enumerate(result.records, start=1)
    )
    return "\n".join(lines)


def _convert_amount(
    record: TransactionRecord,
    *,
    currency: str,
    exchange_rate_provider: ExchangeRateProvider | None,
) -> tuple[Decimal, str | None]:
    record_currency = normalize_currency_code(record.currency)
    if record_currency is None:
        raise ExchangeRateProviderError(f"Unsupported stored currency: {record.currency}")
    if record_currency == currency:
        return record.amount, None
    if exchange_rate_provider is None:
        raise ExchangeRateProviderError("Exchange-rate provider is not configured.")
    conversion = exchange_rate_provider.convert(
        record.amount,
        from_currency=record_currency,
        to_currency=currency,
        date=record.date,
    )
    return conversion.converted_amount, conversion.rate_date


def _filter_records(
    records: Sequence[TransactionRecord],
    filters: StatisticsFilters,
) -> list[TransactionRecord]:
    merchant = filters.merchant.casefold() if filters.merchant else None
    return [
        record
        for record in records
        if (filters.category is None or record.category == filters.category)
        and (
            merchant is None
            or (record.merchant is not None and merchant in record.merchant.casefold())
        )
    ]


def _validate_range(date_range: DateRange) -> None:
    try:
        start = date.fromisoformat(date_range.start_date)
        end = date.fromisoformat(date_range.end_date)
    except ValueError:
        raise ValueError("statistics dates must use YYYY-MM-DD") from None
    if start > end:
        raise ValueError("statistics start date must not follow end date")
    if (end - start).days + 1 > MAX_STATISTICS_RANGE_DAYS:
        raise ValueError("statistics date range must not exceed 366 days")


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= 20:
        raise ValueError("statistics limit must be from 1 to 20")


def _date_range(start: date, end: date) -> DateRange:
    return DateRange(start.isoformat(), end.isoformat())


def _format_period(date_range: DateRange) -> str:
    if date_range.start_date == date_range.end_date:
        return date_range.start_date
    return f"{date_range.start_date} 至 {date_range.end_date}"


def _format_ranked_expense(index: int, expense: RankedExpense) -> str:
    line = f"{index}. {_format_record(expense.record)}"
    record_currency = normalize_currency_code(expense.record.currency)
    if record_currency != expense.currency:
        line += (
            f"（折合 {_format_money(expense.converted_amount)} {expense.currency}，"
            f"汇率日 {expense.rate_date}）"
        )
    return line


def _format_record(record: TransactionRecord) -> str:
    parts = [
        record.date,
        record.category,
        _format_original_money(record.amount),
        record.currency,
    ]
    description = record.merchant or record.note
    if description:
        parts.append(description)
    return " ".join(parts)


def _format_money(amount: Decimal) -> str:
    return format(amount.quantize(Decimal("0.01")), "f")


def _format_original_money(amount: Decimal) -> str:
    return format(amount.normalize(), "f")
