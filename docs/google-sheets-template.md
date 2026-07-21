# Google Sheets Template

## MVP Storage Transactions Sheet

The canonical worksheet name is `Transactions`.

The first row must contain these headers in this exact order:

```text
id,date,amount,currency,type,category,merchant,payment_method,note,source_platform,source_user_id,source_username,source_user_display_name,source_chat_id,source_message_id,created_at,updated_at
```

The versioned code contract for this template lives in `integrations/google_sheets/schema.py`. Repository code should import the sheet name and header constants from that module instead of duplicating column names.

Column meanings:

- `id`: backend-generated stable transaction identifier.
- `date`: transaction date as `YYYY-MM-DD`.
- `amount`: positive decimal transaction amount.
- `currency`: ISO 4217 currency code.
- `type`: transaction type, initially `expense`.
- `category`: normalized supported category.
- `merchant`: merchant or place when known.
- `payment_method`: user-provided payment method when known.
- `note`: user-visible description or extra details.
- `source_platform`: source provider identifier, currently `telegram` or `wechat`.
- `source_user_id`: source provider user identifier.
- `source_username`: source provider username when available.
- `source_user_display_name`: source provider display name when available.
- `source_chat_id`: source provider conversation or official-account identifier.
- `source_message_id`: source provider message identifier.
- `created_at`: backend creation timestamp in the configured timezone.
- `updated_at`: backend update timestamp in the configured timezone.

The sheet contract stores the source platform/user/chat/message tuple for duplicate detection and preserves human-readable provider user metadata for audit.

## Manual Setup

1. Create or open the Google Sheet used for MVP storage.
2. Add a worksheet named `Transactions`.
3. Paste the required header row into row 1.
4. Freeze row 1 if desired for manual inspection.
5. Copy the Sheet ID from the URL and provide it through `GOOGLE_SHEET_ID`.
6. Create a Google service account for the app.
7. Share the Sheet with the service account `client_email` and grant editor access.
8. Provide the service account JSON through `GOOGLE_SERVICE_ACCOUNT_JSON` using an environment variable or secret manager.

Do not commit the service account JSON, downloaded credential files, or `.env` files containing secrets.

## Existing Sheet Migration

Sheets created before the Telegram metadata schema used this suffix:

```text
telegram_user_id,telegram_message_id,created_at,updated_at
```

Before deploying this version, update row 1 so the suffix is:

```text
source_platform,source_user_id,source_username,source_user_display_name,source_chat_id,source_message_id,created_at,updated_at
```

For rows written by the Telegram-only schema, map `telegram_user_id` to
`source_user_id`, `telegram_username` to `source_username`,
`telegram_user_display_name` to `source_user_display_name`, `telegram_chat_id`
to `source_chat_id`, and `telegram_message_id` to `source_message_id`. Set
`source_platform` to `telegram`. Existing rows can leave `source_username` and
`source_user_display_name` blank. For rows written by the older private-only
webhook, set `source_chat_id` to the same value as `source_user_id` so duplicate
detection remains stable after migration. New writes populate all source
metadata columns. The repository intentionally rejects the old header order so a
partially migrated sheet fails before appending or updating rows.

## Validation

`validate_transaction_headers` checks a loaded header row against the required contract and reports missing, reordered, and unexpected headers. Duplicate header occurrences are reported as unexpected. A valid sheet header must exactly match the contract above.

## PostgreSQL Ledger Projection Sheet

The PostgreSQL export tool projects authoritative database rows into Google
Sheets for user visibility and verification. It writes to a separate worksheet
named `Ledger` with this narrower user-facing header row:

```text
id,date,amount,currency,type,category,merchant,payment_method,note,created_at,updated_at
```

The export contract lives in `integrations/google_sheets/ledger_export.py`.
Rows are upserted by `id`, so repeated database -> Google Sheets syncs update
the existing transaction row rather than appending duplicates. Source metadata,
parser internals, raw provider payloads, and location context are intentionally
not included in this projection by default.

The projection must never reuse or rename the legacy `Transactions` worksheet.
That worksheet retains its 17-column schema while the temporary rollback path
exists, including source metadata required for duplicate detection. Before
enabling projection for an existing spreadsheet:

1. Back up or confirm recovery for the existing `Transactions` worksheet.
2. Create a new worksheet named `Ledger`.
3. Paste the 11-column projection header above into `Ledger` row 1.
4. Leave `Transactions` and its 17-column header unchanged.
5. Run one staging projection and verify that only `Ledger` changed.

This separation makes projection schema changes non-destructive and keeps the
legacy rollback repository readable until rollback compatibility is retired.
