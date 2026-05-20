# Google Sheets Template

## Transactions Sheet

The canonical worksheet name is `Transactions`.

The first row must contain these headers in this exact order:

```text
id,date,amount,currency,type,category,merchant,payment_method,note,telegram_user_id,telegram_message_id,created_at,updated_at
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
- `telegram_user_id`: source Telegram user identifier.
- `telegram_message_id`: source Telegram message identifier.
- `created_at`: backend creation timestamp.
- `updated_at`: backend update timestamp.

The MVP sheet contract stores the Telegram user/message pair for duplicate detection. Additional Telegram chat or update metadata requires a future schema migration issue.

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

## Validation

`validate_transaction_headers` checks a loaded header row against the required contract and reports missing, reordered, and unexpected headers. Duplicate header occurrences are reported as unexpected. A valid sheet header must exactly match the contract above.
