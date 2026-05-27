# PostgreSQL Backfill And Cutover

This runbook migrates existing Google Sheets transaction rows into PostgreSQL
and switches production writes only after verification. The migration scripts
default to read-only dry-run behavior; writing to PostgreSQL requires the
explicit `--execute` flag.

## Preconditions

- PostgreSQL migrations have been applied through
  `0002_add_transaction_external_id.sql`.
- `GOOGLE_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`, and `DATABASE_URL` point to
  the intended production resources.
- `DEFAULT_TIMEZONE` matches production, currently `Asia/Singapore`.
- Google Sheets has the `Transactions` worksheet and current headers from
  `docs/google-sheets-template.md`.
- No production storage backend change is made until the verification report is
  clean and an operator approves cutover.

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

Blocking preflight issues must be resolved before cutover:

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

Cutover requires a passing report. If the report fails, keep
`STORAGE_BACKEND=google_sheets`, fix the data or script issue, rerun backfill,
and rerun verification.

## Cutover

After a clean verification report and explicit operator approval:

1. Keep the Google Sheets credentials configured for rollback and manual
   visibility.
2. Set production non-secret config to `STORAGE_BACKEND=postgres`.
3. Provide `DATABASE_URL` through the production secret mapping.
4. Deploy the Cloud Run revision.
5. Verify `/health`.
6. Run `python scripts/verify_postgres_backfill.py` again before any controlled
   test write and record the report in the issue or PR.
7. Send one controlled bot message and confirm the new row appears in
   PostgreSQL.

After the controlled bot message, the full verification report will show that
new PostgreSQL-only row as an extra transaction unless Google Sheets has been
refreshed from a matching export or the report is scoped to the pre-cutover
snapshot.

For Cloud Run, update `CLOUD_RUN_ENV_VARS` to include
`STORAGE_BACKEND=postgres` and update `CLOUD_RUN_SECRET_MAPPINGS` to include
`DATABASE_URL=<secret-name>:<version>`.

## Rollback

Rollback is a runtime configuration change:

1. Set production config back to `STORAGE_BACKEND=google_sheets`.
2. Keep `GOOGLE_SHEET_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON` mapped.
3. Redeploy the Cloud Run revision.
4. Verify `/health`.
5. Send one controlled bot message and confirm the new row appears in Google
   Sheets.

Do not truncate or mutate PostgreSQL during rollback. If production accepted
PostgreSQL-only writes before rollback, reconcile or export those rows before an
extended Google Sheets rollback window.

## Google Sheets Transition State

After cutover, keep Google Sheets as the frozen migration source and optional
manual visibility/export target. Do not edit imported rows manually during the
transition window; manual edits after cutover will not automatically sync back
to PostgreSQL.
