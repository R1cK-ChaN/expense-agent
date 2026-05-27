# Architecture

## System Boundary

Expense Agent has five primary boundaries:

- Telegram and WeChat Official Account are inbound and reply IM interfaces.
- Backend services own orchestration, validation, persistence decisions, and replies.
- The LLM is parser-only and returns structured intent.
- Google Sheets is the MVP storage system.
- PostgreSQL is the durable storage target behind the same repository boundary.
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
2. The parser identifies `query_monthly_total` intent and month/currency filters.
3. The application service validates and bounds the query.
4. The repository reads matching rows from Google Sheets.
5. The application service converts non-default-currency rows with transaction-date exchange rates when a default-currency total is requested.
6. The application service formats totals or a compact transaction list.
7. The platform adapter replies without mutating storage.

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
reply. Unsupported or malformed WeChat messages are acknowledged with
`success` and are not handed to the application service.

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
handler when the parser credentials, parser model, Google service account JSON,
and Google Sheet ID are configured. Without those runtime settings, the app
still imports and serves health checks without external credentials.

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
inside the PostgreSQL integration module and implements the same repository
contract used by `TransactionService`; production runtime wiring remains on the
Google Sheets repository until a later backend-switch issue.

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
reference-rate API. The application service uses the provider only for query
reporting; original transaction amount and currency are never overwritten.

## Data Ownership

- Raw IM text is owned by the provider adapter until it is handed to the application service.
- Parsed intent is owned by the parser port as an untrusted proposal.
- Validated transaction state is owned by the application service and persisted through the repository.
- Google Sheets owns durable MVP storage after a write succeeds in current
  runtime wiring.
- PostgreSQL owns durable relational storage when the PostgreSQL repository is
  selected by future runtime wiring.
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

Required configuration:

- Telegram bot token.
- Telegram webhook secret token.
- WeChat Official Account token for callback signature verification.
- Parser provider credentials and model identifier.
- Google Sheets credentials.
- Google Sheet identifier and worksheet name.
- Default timezone.
- Default currency.

Optional configuration:

- Telegram bot username for group/supergroup mention handling.

Secrets must come from environment variables or a secret manager. They must not be committed to the repository.

The Google Sheet must contain a worksheet named `Transactions` with the required header row described in `docs/google-sheets-template.md`.

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
- WeChat adapter contract: callback verification, text XML normalization, and passive reply XML.
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
tests for signature verification, XML text-message normalization, and passive
reply XML.

The Google Sheets repository contract is covered with an in-memory Sheets client
so duplicate lookup, latest lookup, update, monthly sum, schema validation, and
provider failure mapping can be tested without real credentials.

The PostgreSQL repository contract is covered with an in-memory psycopg-like
connection so atomic create, idempotency lookup, latest lookup, update events,
monthly queries, schema expectations, and repository failure mapping can be
tested without real database credentials.

The domain validation contract is covered with unit tests for missing and
non-positive amounts, timezone-based date defaults, default currency, category
fallback, expense-only type enforcement, and multiple-expense rejection.

The create-expense application service contract is covered with fake parser and
repository tests for successful appends, configured defaults, relative dates,
missing amount, duplicate provider retries, low-confidence parser output,
unknown intent, parser failure, and Google Sheets write failure. App bootstrap
tests also cover the configured runtime wiring from webhook message to sheet
append and Telegram reply.
