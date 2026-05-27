from decimal import Decimal

from integrations.google_sheets.repository import TransactionRecord
from scripts.verify_postgres_backfill import (
    compare_backfill_records,
    format_verification_report,
)


def test_compare_backfill_records_passes_matching_counts_aggregates_and_latest():
    source_records = [
        make_record(transaction_id="txn-1", amount=Decimal("12.30")),
        make_record(
            transaction_id="txn-2",
            amount=Decimal("5.00"),
            currency="USD",
            category="交通",
            merchant="Taxi",
            source_message_id="9002",
            created_at="2026-05-20T10:00:00+00:00",
            updated_at="2026-05-20T10:00:00+00:00",
        ),
    ]

    report = compare_backfill_records(source_records, list(source_records))

    assert report.is_success
    assert report.source_count == 2
    assert report.target_count == 2
    assert report.missing_ids == ()
    assert report.extra_ids == ()
    assert report.record_mismatches == ()
    assert report.monthly_total_mismatches == ()
    assert report.currency_count_mismatches == ()
    assert report.category_count_mismatches == ()
    assert report.merchant_count_mismatches == ()
    assert report.latest_transaction_mismatches == ()
    assert "verification passed" in format_verification_report(report)


def test_compare_backfill_records_ignores_mutable_identity_display_names():
    source_records = [
        make_record(
            transaction_id="txn-1",
            source_username="old-name",
            source_user_display_name="Old Name",
        )
    ]
    target_records = [
        make_record(
            transaction_id="txn-1",
            source_username="new-name",
            source_user_display_name="New Name",
        )
    ]

    report = compare_backfill_records(source_records, target_records)

    assert report.is_success
    assert report.record_mismatches == ()


def test_compare_backfill_records_detects_latest_tie_breaker_differences():
    source_records = [
        make_record(transaction_id="txn-1", source_message_id="9001"),
        make_record(transaction_id="txn-2", source_message_id="9002"),
    ]
    target_records = list(source_records)

    report = compare_backfill_records(source_records, target_records)

    assert not report.is_success
    assert report.latest_transaction_mismatches[0].expected == "txn-1"
    assert report.latest_transaction_mismatches[0].actual == "txn-2"


def test_compare_backfill_records_reports_record_and_aggregate_differences():
    source_records = [
        make_record(
            transaction_id="txn-1",
            amount=Decimal("12.30"),
            category="餐饮",
            merchant="coffee shop",
            created_at="2026-05-19T10:00:00+00:00",
        ),
        make_record(
            transaction_id="txn-2",
            amount=Decimal("5.00"),
            category="交通",
            merchant="Taxi",
            source_message_id="9002",
            created_at="2026-05-20T10:00:00+00:00",
        ),
    ]
    target_records = [
        make_record(
            transaction_id="txn-1",
            amount=Decimal("99.00"),
            category="购物",
            merchant="mall",
            created_at="2026-05-19T10:00:00+00:00",
        ),
        make_record(
            transaction_id="txn-3",
            amount=Decimal("7.00"),
            currency="USD",
            category="交通",
            merchant="Taxi",
            source_message_id="9003",
            created_at="2026-05-21T10:00:00+00:00",
        ),
    ]

    report = compare_backfill_records(source_records, target_records)

    assert not report.is_success
    assert report.missing_ids == ("txn-2",)
    assert report.extra_ids == ("txn-3",)
    assert report.record_mismatches[0].transaction_id == "txn-1"
    assert set(report.record_mismatches[0].field_differences) == {
        "amount",
        "category",
        "merchant",
    }
    assert report.monthly_total_mismatches
    assert report.currency_count_mismatches
    assert report.category_count_mismatches
    assert report.merchant_count_mismatches
    assert report.latest_transaction_mismatches
    rendered = format_verification_report(report)
    assert "verification failed" in rendered
    assert "missing transaction ids: txn-2" in rendered
    assert "extra PostgreSQL transaction ids: txn-3" in rendered


def make_record(
    *,
    transaction_id: str = "txn-1",
    date: str = "2026-05-19",
    amount: Decimal = Decimal("12.30"),
    currency: str = "SGD",
    transaction_type: str = "expense",
    category: str = "餐饮",
    merchant: str | None = "coffee shop",
    payment_method: str | None = "card",
    note: str | None = "lunch",
    source_platform: str = "telegram",
    source_user_id: str = "42",
    source_username: str | None = "ada",
    source_user_display_name: str | None = "Ada Lovelace",
    source_chat_id: str = "12345",
    source_message_id: str = "9001",
    created_at: str = "2026-05-19T10:00:00+00:00",
    updated_at: str = "2026-05-19T10:00:00+00:00",
) -> TransactionRecord:
    return TransactionRecord(
        id=transaction_id,
        date=date,
        amount=amount,
        currency=currency,
        type=transaction_type,
        category=category,
        merchant=merchant,
        payment_method=payment_method,
        note=note,
        source_platform=source_platform,
        source_user_id=source_user_id,
        source_username=source_username,
        source_user_display_name=source_user_display_name,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        created_at=created_at,
        updated_at=updated_at,
    )
