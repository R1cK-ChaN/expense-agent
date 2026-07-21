# PostgreSQL Backfill, Verification, And Cutover

PostgreSQL is the authoritative ledger target. This runbook imports the legacy
Google Sheets ledger, verifies the resulting database state, enables PostgreSQL
in staging, and defines the separate production cutover and rollback decisions.
The migration scripts default to read-only dry-run behavior; writing to
PostgreSQL requires the explicit `--execute` flag. Running a backfill or passing
verification does not by itself authorize production exposure.

## Preconditions

- PostgreSQL migrations have been applied through
  `0002_add_transaction_external_id.sql`.
- `GOOGLE_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`, and `DATABASE_URL` point to
  the explicitly approved source and target environment.
- `DEFAULT_TIMEZONE` matches the source ledger, currently `Asia/Singapore`.
- Google Sheets has the `Transactions` worksheet and current headers from
  `docs/google-sheets-template.md`.
- No production bot configuration changes without explicit approval.

Validate local migration files:

```sh
python scripts/migrate_postgres.py --check
```

Apply migrations to the target database:

```sh
python scripts/migrate_postgres.py --database-url "$DATABASE_URL"
```

## Backfill

Run a dry-run first. This reads Google Sheets, checks each row for idempotent
source metadata, checks for duplicate transaction IDs/source message tuples, and
checks whether PostgreSQL already has equivalent rows.

```sh
python scripts/backfill_google_sheets_to_postgres.py
```

The dry-run reports:

- Total Google Sheets transaction rows.
- Rows already present in PostgreSQL.
- Rows pending import.
- Any blocking preflight issues.

If preflight is clean, execute the import:

```sh
python scripts/backfill_google_sheets_to_postgres.py --execute
```

The backfill uses the PostgreSQL repository append path, so each imported row
creates or reuses the internal user and provider identity, inserts an inbound
message idempotency row, inserts the transaction, and appends a `created`
transaction event. Re-running the command skips equivalent existing rows.

Blocking preflight issues must be resolved before executing the import:

- Duplicate transaction IDs.
- Duplicate source message tuples.
- Missing source platform, user, chat, or message metadata.
- Non-expense rows, because the current PostgreSQL schema is expense-only.
- Existing PostgreSQL rows with the same source message but different values.

## Verification

Run the verification report after backfill:

```sh
python scripts/verify_postgres_backfill.py
```

The report compares Google Sheets against PostgreSQL for:

- Total row counts.
- Missing and extra transaction IDs.
- Field-level row mismatches, excluding mutable username/display-name metadata
  stored on the shared identity row.
- Monthly totals by source user and currency.
- Currency, category, and merchant counts.
- Latest expense transaction per source user.

Treat a failing report as an invalid copy: fix the data or script issue, rerun
the backfill, and rerun verification. Keep runtime on its current backend.

## Staging Validation

1. Deploy with `STORAGE_BACKEND=postgres` and a staging `DATABASE_URL`.
2. Create one controlled expense and confirm the transaction and `created`
   event committed before the reply.
3. Update it and confirm the current row plus `updated` event.
4. Replay the provider message and confirm no duplicate transaction or event.
5. Query the containing date range and verify totals, rate dates, and category
   percentages.
6. Configure `google_sheet_exports`, run
   `scripts/sync_postgres_to_google_sheets.py`, and verify the projected row.
7. Simulate a Sheet failure, verify `last_error` and the unchanged cursor, then
   retry successfully.

## Production Cutover

Production cutover requires explicit approval after the backfill verification
and staging validation above succeed. Set `STORAGE_BACKEND=postgres`, provide
`DATABASE_URL`, deploy, and repeat the controlled create/update/replay/query and
projection checks. A successful deploy or health check alone is not approval to
expose PostgreSQL-backed behavior.

## Rollback

Restore `STORAGE_BACKEND=google_sheets` with the legacy Sheet credentials and
stop the projection job so there is only one Sheet writer. Before resuming
normal traffic, identify PostgreSQL transactions committed during the cutover
window and reconcile them into the Sheet without changing their stable IDs.
Preserve PostgreSQL and its audit events for investigation; do not run a
destructive migration.
