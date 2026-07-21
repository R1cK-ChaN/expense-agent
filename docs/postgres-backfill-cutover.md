# PostgreSQL Backfill And Verification (Offline)

> Historical migration runbook: PostgreSQL is no longer a selectable ledger
> backend for the business bot. Google Sheets is the canonical source of truth.
> The PostgreSQL commands below are retained only for offline migration,
> verification, and export operations. They do not change the bot's source of
> truth or authorize a production cutover.

This runbook imports existing Google Sheets transaction rows into an offline
PostgreSQL database and verifies the resulting copy. The migration scripts
default to read-only dry-run behavior; writing to PostgreSQL requires the
explicit `--execute` flag. Google Sheets remains the canonical ledger before,
during, and after these commands.

## Preconditions

- PostgreSQL migrations have been applied through
  `0002_add_transaction_external_id.sql`.
- `GOOGLE_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`, and `DATABASE_URL` point to
  the explicitly approved source and offline verification target.
- `DEFAULT_TIMEZONE` matches the source ledger, currently `Asia/Singapore`.
- Google Sheets has the `Transactions` worksheet and current headers from
  `docs/google-sheets-template.md`.
- No production bot configuration is changed by this workflow.

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

Blocking preflight issues must be resolved before executing the offline import:

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

Treat a failing report as an invalid offline copy: fix the data or script issue,
rerun the backfill, and rerun verification. It has no effect on the canonical
Google Sheets ledger.

## Retired Production Cutover

The former `STORAGE_BACKEND=postgres` Cloud Run cutover and configuration-based
rollback procedure is retired. `app/main.py` always builds the Google Sheets
repository for bot traffic, and the deploy script requires Google Sheets
configuration. Do not set `STORAGE_BACKEND=postgres` expecting it to redirect
runtime reads or writes.

If PostgreSQL runtime ownership is proposed again, treat it as a new
architecture and release change: update application wiring, compatibility
tests, current-state documentation, post-deploy validation, and rollback plans
in a separately approved issue. An offline import or clean verification report
is evidence about data compatibility only; it is not a release gate or cutover
authorization.
