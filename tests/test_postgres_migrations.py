import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "0001_initial_schema.sql"
EXTERNAL_ID_MIGRATION_PATH = (
    ROOT / "migrations" / "0002_add_transaction_external_id.sql"
)
SHEET_EXPORT_MIGRATION_PATH = ROOT / "migrations" / "0003_google_sheets_export.sql"
FUNCTION_BATCH_MIGRATION_PATH = (
    ROOT / "migrations" / "0004_function_call_batches.sql"
)


def _migration_sql() -> str:
    return MIGRATION_PATH.read_text()


def test_initial_postgres_migration_creates_core_tables():
    sql = _migration_sql()

    for table_name in (
        "users",
        "user_identities",
        "inbound_messages",
        "transactions",
        "transaction_events",
    ):
        assert f"create table {table_name}" in sql


def test_inbound_messages_have_provider_idempotency_and_wechat_metadata():
    sql = _migration_sql()

    assert (
        "unique (platform, platform_chat_id, provider_dedupe_key)"
        in sql
    )
    assert "provider_message_type text not null" in sql
    assert "provider_event_type text" in sql
    assert "normalized_text text" in sql
    assert "provider_message_type in (" in sql
    for message_type in ("text", "voice", "location", "event", "unsupported"):
        assert f"'{message_type}'" in sql


def test_users_can_store_latest_location_context():
    sql = _migration_sql()

    assert "last_latitude numeric(9, 6)" in sql
    assert "last_longitude numeric(9, 6)" in sql
    assert "last_location_updated_at timestamptz" in sql


def test_transactions_can_optionally_link_to_source_inbound_message():
    sql = _migration_sql()

    assert "created_from_message_id uuid references inbound_messages(id)" in sql
    assert "created_from_message_id uuid not null" not in sql
    assert "unique (created_from_message_id)" in sql


def test_transaction_external_id_migration_preserves_domain_ids():
    sql = EXTERNAL_ID_MIGRATION_PATH.read_text()

    assert "add column external_id text" in sql
    assert "set external_id = id::text" in sql
    assert "alter column external_id set not null" in sql
    assert "transactions_external_id_key unique (external_id)" in sql


def test_google_sheets_export_migration_adds_per_user_sync_config():
    sql = SHEET_EXPORT_MIGRATION_PATH.read_text().lower()

    assert "create table google_sheet_exports" in sql
    assert "user_id uuid primary key references users(id)" in sql
    assert "spreadsheet_id text not null" in sql
    assert "enabled boolean not null default true" in sql
    assert "last_synced_event_id uuid references transaction_events(id)" in sql
    assert "last_synced_at timestamptz" in sql
    assert "last_error text" in sql
    assert "idx_google_sheet_exports_enabled" in sql


def test_function_batch_migration_preserves_legacy_single_message_constraint():
    sql = FUNCTION_BATCH_MIGRATION_PATH.read_text().lower()

    assert "drop constraint transactions_created_from_message_id_key" not in sql
    assert "add column function_batch_id uuid" in sql
    assert "add column function_call_index integer" in sql
    assert "unique (function_batch_id, function_call_index)" in sql


def test_function_batch_migration_adds_delivery_idempotency_and_results():
    sql = FUNCTION_BATCH_MIGRATION_PATH.read_text().lower()

    assert "create table function_call_batches" in sql
    assert "inbound_message_id uuid not null unique references inbound_messages(id)" in sql
    assert "accepted_calls jsonb not null" in sql
    assert "reply_text text" in sql
    assert (
        "status in ('accepted', 'writes_committed', 'completed', 'failed')"
        in sql
    )
    assert "create table function_call_executions" in sql
    assert "primary key (function_batch_id, function_call_index)" in sql


def test_function_batch_migration_adds_one_expiring_pending_request_per_chat():
    sql = FUNCTION_BATCH_MIGRATION_PATH.read_text().lower()

    assert "create table pending_requests" in sql
    assert "unique (identity_id, platform_chat_id)" in sql
    assert "known_arguments jsonb not null" in sql
    assert "missing_fields text[] not null" in sql
    assert "expires_at timestamptz not null" in sql


def test_transaction_events_support_append_only_audit_history():
    sql = _migration_sql()

    assert "create table transaction_events" in sql
    assert "transaction_id uuid not null references transactions(id)" in sql
    assert "message_id uuid references inbound_messages(id)" in sql
    assert "old_values jsonb" in sql
    assert "new_values jsonb" in sql
    assert "event_type in ('created', 'updated', 'corrected', 'deleted')" in sql


def test_initial_postgres_migration_adds_query_path_indexes():
    sql = _migration_sql()

    for index_name in (
        "idx_user_identities_user_id",
        "idx_inbound_messages_user_received",
        "idx_inbound_messages_user_type_received",
        "idx_transactions_user_created",
        "idx_transactions_user_date",
        "idx_transactions_user_month",
        "idx_transaction_events_transaction",
    ):
        assert f"create index {index_name}" in sql


def test_postgres_migration_check_command_exists():
    runner = ROOT / "scripts" / "migrate_postgres.py"

    assert runner.exists()


def test_postgres_migration_check_command_validates_local_files():
    runner = ROOT / "scripts" / "migrate_postgres.py"

    result = subprocess.run(
        [sys.executable, str(runner), "--check"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "0001_initial_schema.sql" in result.stdout
    assert "0002_add_transaction_external_id.sql" in result.stdout
    assert "0003_google_sheets_export.sql" in result.stdout
    assert "0004_function_call_batches.sql" in result.stdout
