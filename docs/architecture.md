# Architecture

## System Boundary

Expense Agent has six primary boundaries:

- Telegram and WeChat Official Account are inbound and reply IM interfaces.
- Backend services own orchestration, validation, persistence decisions, and replies.
- The LLM is parser-only and returns structured intent.
- Google Sheets is the canonical, user-visible ledger for bot runtime traffic.
- PostgreSQL repository, backfill, verification, and export modules are retained
  for offline tooling; they are not selectable as the bot's runtime ledger.
- The database-to-Sheets export path is an operator-run offline tool, not a
  second source of truth or part of the bot request path.
- The exchange-rate provider supplies deterministic historical rates for reporting.

The backend is the only component allowed to decide whether data is valid, whether storage should change, and what IM reply should be sent.

## Runtime Flow

### Create Transaction

1. Telegram or WeChat sends a text message to the platform webhook.
2. The platform adapter extracts message text and source metadata.
3. The application service checks whether the source platform/user/chat/message tuple already created a transaction.
4. The application service sends the raw text and relevant defaults to the parser port.
5. The parser returns a structured parser result.
6. The application service validates the parser result against the domain model.
7. Before append, the application service checks the user's latest recent expense for an exact currency-correction retry.
8. The Google Sheets repository appends one transaction row when validation passes and no duplicate exists, or updates the recent row when the retry guard matches.
9. The application service formats a confirmation or clarification reply.
10. The platform adapter sends or returns the reply to the originating conversation.

### Update Transaction

1. Telegram or WeChat receives an update request.
2. The parser identifies `update_recent_expense` intent and requested changes.
3. The application service resolves the target transaction through the repository.
4. The application service validates that exactly one target exists and every changed field is valid.
5. The repository updates the existing row and preserves source metadata.
6. The bot replies with the updated summary or a clarification prompt.

### Query Transactions

1. Telegram or WeChat receives a query request.
2. The parser identifies `query_monthly_total` intent and inclusive start/end dates.
3. The application service validates and bounds the query.
4. The repository reads matching rows from Google Sheets.
5. The application service converts non-default-currency rows with transaction-date exchange rates.
6. The application service formats the local-currency total, original foreign-currency subtotals, actual exchange-rate dates, and local-currency category amounts and percentages.
7. The platform adapter replies without mutating storage.

### Google Sheets Export Projection

1. A PostgreSQL operator configures one `google_sheet_exports` row per internal
   user with the user's Google spreadsheet ID.
2. The sync runner reads enabled export configs and pending transaction events
   after each user's `last_synced_event_id`.
3. The runner maps the current database transaction state to user-facing ledger
   fields only.
4. The Google Sheets export adapter upserts the `Transactions` row by stable
   transaction ID, so repeated syncs or update events do not duplicate rows.
5. On success, PostgreSQL advances `last_synced_event_id`, sets
   `last_synced_at`, and clears `last_error`.
6. On Google Sheets failure, the already committed database transaction remains
   unchanged and PostgreSQL records `last_error` for retry and inspection.

This path is strictly database -> Google Sheets. Manual sheet edits are never
read back as transaction input.

## Component Responsibilities

### Telegram and WeChat Adapters

Owns:

- Receiving Telegram updates.
- Receiving WeChat Official Account callback and message webhooks.
- Extracting message text and provider metadata.
- Normalizing provider payloads into `InboundMessage`.
- Sending or returning replies through the provider-specific mechanism.
- Translating provider delivery errors into backend errors.

Does not own:

- Parsing natural language.
- Validating transaction rules.
- Writing Google Sheets rows.
- Business decisions about supported intents.

The shared inbound message contract lives in `core/messages.py` as
`InboundMessage`. Telegram and WeChat adapters map provider payloads into this
shape with `source_platform`, `source_user_id`, `source_chat_id`,
`source_message_id`, raw text, optional display metadata, and received
timestamp fields.

The Telegram webhook adapter lives in `app/telegram_webhook.py` and exposes
`POST /telegram/webhook`. It supports private text messages and group or
supergroup text messages that explicitly mention the configured bot username.
For groups, the bot mention is stripped before parser input. Group messages
without the bot mention are acknowledged and ignored. Private non-text messages
receive the fixed unsupported message reply. The route requires Telegram's
`X-Telegram-Bot-Api-Secret-Token` header to match the configured webhook secret
before any update processing or reply sending occurs.

The outbound Telegram client lives in `integrations/telegram_client.py`. It
wraps the Bot API `sendMessage` method, takes the bot token from runtime
configuration, and accepts an injectable JSON transport for contract tests.

The WeChat Official Account webhook adapter lives in `app/wechat_webhook.py`
and exposes `GET /wechat/webhook` for callback verification plus
`POST /wechat/webhook` for message delivery. GET verification checks WeChat's
SHA-1 signature over the configured token, timestamp, and nonce before
returning `echostr`. POST verifies the same signature, parses text XML
messages, normalizes them into `InboundMessage`, and returns a passive text XML
reply. Voice XML messages with WeChat `Recognition` are normalized through the
same text-message path; the backend does not download audio or run ASR. Voice
messages without recognition return a short clarification and do not call the
parser. User-sent `location` messages and automatic `LOCATION` events are routed
to an injectable location handler for latest-location persistence when available
and are never sent to the expense parser or used to infer currency/category.
`subscribe` events return a welcome message. Unsupported or malformed WeChat
messages are acknowledged with `success` and are not handed to the application
service.

### Application Service

Owns:

- Routing parser intents to create, update, query, or unsupported handlers.
- Applying configured defaults such as timezone and currency.
- Validating domain invariants before storage writes.
- Applying valid supported update fields while ignoring unsupported
  parser-proposed fields when at least one safe change remains.
- Enforcing idempotency for IM message processing.
- Formatting confirmation, clarification, empty-result, and error replies.
- Coordinating repositories, parser ports, and exchange-rate providers.

Does not own:

- Provider-specific Telegram or WeChat HTTP/XML details.
- Prompt wording or LLM provider internals.
- Google Sheets API details.
- Provider-specific exchange-rate HTTP details.

Create-expense orchestration lives in `core/transaction_service.py`. The
service accepts normalized IM source metadata, checks the repository for an
existing `source_platform`, `source_user_id`, `source_chat_id`, and
`source_message_id` before parsing, sends the normalized text to the parser,
validates create-expense output, appends one transaction row, and returns the
reply text for the platform adapter to send.
Low-confidence create-expense parser results produce a clarification reply and
do not write to storage.
Duplicate provider retries return the stored transaction confirmation without a
second append.

`app/main.py` wires this service as the default Telegram and WeChat text
handler when parser credentials, parser model, Google service-account JSON,
and Sheet ID are configured. The bot always constructs the Google Sheets
repository; legacy `STORAGE_BACKEND` and `DATABASE_URL` settings do not override
the runtime ledger. Without the required Google Sheets settings, the app skips
transaction handling and still imports and serves health checks without
external credentials.

### Domain Validation

Owns:

- Treating parser output as untrusted input before create-expense writes.
- Applying configured defaults for date, currency, type, and category.
- Enforcing positive amount, valid date, supported mainstream currency, expense-only MVP type, supported update fields, and single-record MVP behavior.
- Returning explicit validation error codes and user-facing messages for correctable failures.

Does not own:

- Prompt wording or LLM provider behavior.
- Generating repository identifiers or storage timestamps.
- Sending IM replies or appending Google Sheets rows.

The create-expense validator lives in `core/validator.py`. It accepts typed
parser results plus runtime defaults, returns either a `ValidatedExpense` or a
validation failure, and never mutates storage. Shared category constants live in
`core/categories.py` so parser and validator code use the same allowlist without
making the validator depend on parser prompt details.
Supported currency constants and aliases live in `core/currencies.py`; missing
create-expense currency defaults to the configured currency, explicit supported
currencies are preserved, currency update aliases normalize to their canonical
codes, and unsupported currency values fail validation before storage writes.

### Parser Port

Owns:

- Turning raw user text into a structured parser result.
- Returning confidence and missing-field information.
- Normalizing supported categories and currencies when confidence is sufficient.

Does not own:

- Calling Google Sheets.
- Sending Telegram or WeChat messages.
- Deciding that a transaction should be persisted.
- Performing updates, deletes, or queries.
- Performing exchange-rate conversion.
- Running background agent loops or invoking arbitrary tools.

The parser contract lives in `core/intent_parser.py`. It builds the parser-only
system prompt, sends raw text plus date/currency defaults to an injectable LLM
client, and strictly validates the JSON response before returning typed parser
results. Malformed provider output is converted into a controlled parser failure
without mutating storage or sending IM messages. Create-expense categories
outside the canonical allowlist are preserved for domain validation, where they
default to `未分类` instead of being treated as provider-shape failures.

Provider-specific chat completion HTTP code lives in `integrations/llm_client.py`.
The adapter uses an OpenAI-compatible JSON chat-completions request and exposes
only the `complete_json` method required by the parser port.

### Google Sheets Repository

Owns:

- Appending validated transaction rows.
- Updating exactly one resolved transaction row.
- Looking up transactions for idempotency, update resolution, and queries.
- Mapping domain objects to the stable `Transactions` sheet contract in `integrations/google_sheets/schema.py`.
- Translating Google Sheets API failures into explicit repository errors.

Does not own:

- Natural-language parsing.
- IM reply formatting.
- Domain validation beyond defensive repository checks.

The repository implementation lives in `integrations/google_sheets/repository.py`.
Application services depend on `GoogleSheetsTransactionRepository` and its
`SheetsValuesClient` boundary rather than calling Google API resources directly.
The concrete `GoogleSheetsValuesClient` wraps the Sheets `spreadsheets().values()`
API for row reads, appends, and updates.

### PostgreSQL Repository

Owns:

- Persisting inbound message idempotency rows, current transaction rows, and
  transaction audit events in one database transaction for creates.
- Mapping provider identities to internal users.
- Looking up transactions by source message, latest user expense, and monthly
  expense date ranges.
- Updating supported transaction fields and appending an update audit event in
  one database transaction.
- Preserving the existing domain-facing `TransactionRecord.id` through the
  `transactions.external_id` column while keeping UUIDs as internal database
  keys.
- Translating database failures into repository errors.

Does not own:

- Runtime backend selection.
- Natural-language parsing.
- IM reply formatting.
- Google Sheets data backfill.

The implementation lives in `integrations/postgres/repository.py`. It keeps SQL
inside the PostgreSQL integration module and implements the transaction
repository behaviors needed by offline migration and verification commands.
`app/main.py` does not wire it into bot runtime traffic.

### PostgreSQL Backfill And Verification Scripts

Owns:

- Reading existing Google Sheets transaction rows through the Google Sheets
  repository.
- Preflighting duplicate transaction IDs, duplicate source message tuples,
  missing source metadata, unsupported non-expense rows, and conflicting
  PostgreSQL rows.
- Importing rows into PostgreSQL through the PostgreSQL repository only when
  `--execute` is explicitly supplied.
- Comparing Google Sheets and PostgreSQL row counts, row values, monthly totals,
  currency/category/merchant counts, and latest expense records.
- Preserving the retired production-cutover procedure as clearly labeled
  historical context.

Does not own:

- Parser behavior.
- Runtime request handling.
- Production bot configuration changes.
- Changing Google Sheets as the canonical ledger.

The backfill command lives in
`scripts/backfill_google_sheets_to_postgres.py` and defaults to dry-run mode.
The verification command lives in `scripts/verify_postgres_backfill.py`.
Operational steps are documented in `docs/postgres-backfill-cutover.md`.

### Google Sheets Export Sync

Owns:

- Mapping each internal user to one configured Google spreadsheet through
  `google_sheet_exports`.
- Reading committed PostgreSQL transaction events after the per-user sync
  cursor.
- Upserting user-facing ledger fields into Google Sheets by transaction ID.
- Recording `last_synced_event_id`, `last_synced_at`, and `last_error` for
  retry and inspection.

Does not own:

- Creating or updating database transactions.
- Treating Google Sheets as authoritative input.
- Syncing parser internals, raw provider payloads, location context, or source
  metadata by default.
- Managing spreadsheet sharing or end-user onboarding UX.

The shared export data contract lives in `core/sheet_export.py`. Sync
orchestration lives in `core/sheet_export_service.py`. PostgreSQL export config
and pending event reads live in
`integrations/postgres/sheet_export_repository.py`. The Google Sheets projection
adapter lives in `integrations/google_sheets/ledger_export.py` and writes a
compact `Transactions` sheet with `id`, date, amount, currency, category,
merchant, payment method, note, and timestamps. Operators can run
`scripts/sync_postgres_to_google_sheets.py` manually or from a scheduler.

### Exchange-Rate Provider

Owns:

- Fetching historical daily exchange rates for supported currency pairs.
- Returning the actual rate date used, including latest previous available rates.
- Translating provider failures into explicit exchange-rate errors.

Does not own:

- Parsing user text.
- Mutating transactions.
- Choosing which transactions belong in a summary.

The provider contract lives in `core/exchange_rates.py`. The production adapter
lives in `integrations/exchange_rates.py` and uses Frankfurter's public daily
reference-rate API. The application service uses the provider for foreign-
currency confirmations and query reporting; original transaction amount and
currency are never overwritten.

## Data Ownership

- Raw IM text is owned by the provider adapter until it is handed to the application service.
- Parsed intent is owned by the parser port as an untrusted proposal.
- Validated transaction state is owned by the application service and persisted through the repository.
- Google Sheets owns the durable, canonical bot ledger.
- PostgreSQL stores offline migration, verification, and export data only; it
  does not own production bot transactions.
- Database-to-Sheets export tooling must not be interpreted as changing ledger
  ownership or enabling PostgreSQL runtime selection.
- Exchange-rate conversions are transient reporting data owned by the application service reply path.

Parser results should be treated as untrusted input. The backend must validate every field before writing to storage.

## Error Handling

Expected user-correctable errors:

- Missing amount.
- Invalid amount.
- Unsupported currency.
- Unsupported intent.
- Ambiguous update target.
- Empty query result.

Expected system errors:

- Telegram API failure.
- WeChat callback signature or XML handling failure.
- Parser provider timeout or malformed parser response.
- Google Sheets API failure.
- Exchange-rate provider failure.
- Missing or invalid runtime configuration.

User-correctable errors should produce a clear IM reply and no storage mutation. System errors should produce a generic failure reply, preserve enough logs for debugging, and avoid duplicate writes on retry.

## Configuration

Required configuration for transaction handling:

- Telegram bot token.
- Telegram webhook secret token.
- WeChat Official Account token for callback signature verification.
- Parser provider credentials and model identifier.
- Google Sheets credentials, Sheet ID, and worksheet name.
- For optional offline PostgreSQL commands, `DATABASE_URL` in the operator's
  environment; it is not required by the deployed bot.
- For optional `sync_postgres_to_google_sheets.py` runs, a Google service
  account JSON plus one
  enabled `google_sheet_exports` row per user that should receive a Sheets
  ledger projection.
- Default timezone.
- Default currency.

Optional configuration:

- Telegram bot username for group/supergroup mention handling.

Secrets must come from environment variables or a secret manager. They must not be committed to the repository.

The Google Sheet must contain a worksheet named `Transactions` with the required
header row described in `docs/google-sheets-template.md`.

## Delivery

GitHub Actions provides the delivery boundary for this service. Pull requests
run the repository test suite before merge. Pushes to `main` authenticate to
Google Cloud with Workload Identity Federation, build the Dockerfile with Cloud
Build, deploy the image to Cloud Run, and check `/health`.

Runtime secret values remain in GCP Secret Manager. The deploy workflow accepts
only secret names and versions through `CLOUD_RUN_SECRET_MAPPINGS`; it does not
store Telegram, WeChat, parser, or Google credential values in GitHub.

## Testable Contracts

Future implementation should keep these contracts independently testable:

- Parser contract: raw text to parser result.
- Domain validation contract: parser result plus defaults to valid command or clarification.
- Repository contract: transaction append, update, lookup, and query behavior.
- Telegram adapter contract: Telegram update to source metadata and reply call.
- WeChat adapter contract: callback verification, text/voice XML normalization,
  location/event no-parser routing, and passive reply XML.
- Application service contract: orchestration across parser, validation, repository, and replies.

The parser contract is covered with fake LLM client tests for create expense,
update recent expense, monthly total query, unknown messages, missing fields,
supported categories, and malformed LLM output. The LLM provider adapter is
covered with an injectable transport so request payloads and provider response
mapping can be tested without real credentials.

The Telegram webhook contract is covered with FastAPI request tests and a fake
reply client. The Telegram Bot API client contract is covered with a fake JSON
transport so the tested payload targets the originating `chat_id` without using
real credentials. The WeChat webhook contract is covered with FastAPI request
tests for signature verification, XML text-message normalization, voice
recognition routing, voice-recognition failure replies, location/event
no-parser behavior, subscription replies, and passive reply XML.

The Google Sheets repository contract is covered with an in-memory Sheets client
so duplicate lookup, latest lookup, update, monthly sum, schema validation, and
provider failure mapping can be tested without real credentials.

The offline PostgreSQL repository contract is covered with an in-memory
psycopg-like connection so atomic create, idempotency lookup, latest lookup,
update events, monthly queries, schema expectations, and repository failure
mapping can be tested without real database credentials.

The Google Sheets export sync contract is covered with fake repositories and an
in-memory Sheets client for per-user spreadsheet routing, transaction-ID
upserts, user-facing field projection, failure status recording, and
`google_sheet_exports` migration expectations.

The domain validation contract is covered with unit tests for missing and
non-positive amounts, timezone-based date defaults, default currency, category
fallback, expense-only type enforcement, and multiple-expense rejection.

The create-expense application service contract is covered with fake parser and
repository tests for successful appends, configured defaults, relative dates,
missing amount, duplicate provider retries, low-confidence parser output,
unknown intent, parser failure, and Google Sheets write failure. App bootstrap
tests cover Google Sheets runtime wiring from webhook message to repository
append and Telegram reply, verify that legacy PostgreSQL settings cannot
override the Sheets ledger, and preserve the safe health-only fallback when
required Google Sheets configuration is missing.
