# Architecture

## System Boundary

Expense Agent has four primary boundaries:

- Telegram is the user interface.
- Backend services own orchestration, validation, persistence decisions, and replies.
- The LLM is parser-only and returns structured intent.
- Google Sheets is the MVP storage system.

The backend is the only component allowed to decide whether data is valid, whether storage should change, and what Telegram reply should be sent.

## Runtime Flow

### Create Transaction

1. Telegram sends an update to the bot webhook or polling process.
2. The Telegram adapter extracts message text and metadata.
3. The application service sends the raw text and relevant defaults to the parser port.
4. The parser returns a structured parser result.
5. The application service validates the parser result against the domain model.
6. The application service checks whether the Telegram message already created a transaction.
7. The Google Sheets repository appends one transaction row when validation passes and no duplicate exists.
8. The application service formats a confirmation reply.
9. The Telegram adapter sends the reply to the originating chat.

### Update Transaction

1. Telegram receives an update request.
2. The parser identifies `update_transaction` intent, target reference, and requested changes.
3. The application service resolves the target transaction through the repository.
4. The application service validates that exactly one target exists and every changed field is valid.
5. The repository updates the existing row and preserves source metadata.
6. The bot replies with the updated summary or a clarification prompt.

### Query Transactions

1. Telegram receives a query request.
2. The parser identifies `query_transactions` intent and filters.
3. The application service validates and bounds the query.
4. The repository reads matching rows from Google Sheets.
5. The application service formats totals or a compact transaction list.
6. The bot replies without mutating storage.

## Component Responsibilities

### Telegram Adapter

Owns:

- Receiving Telegram updates.
- Extracting message text and Telegram metadata.
- Sending replies to Telegram.
- Translating Telegram delivery errors into backend errors.

Does not own:

- Parsing natural language.
- Validating transaction rules.
- Writing Google Sheets rows.
- Business decisions about supported intents.

### Application Service

Owns:

- Routing parser intents to create, update, query, or unsupported handlers.
- Applying configured defaults such as timezone and currency.
- Validating domain invariants before storage writes.
- Enforcing idempotency for Telegram update processing.
- Formatting confirmation, clarification, empty-result, and error replies.
- Coordinating repositories and parser ports.

Does not own:

- Provider-specific Telegram HTTP details.
- Prompt wording or LLM provider internals.
- Google Sheets API details.

### Parser Port

Owns:

- Turning raw user text into a structured parser result.
- Returning confidence and missing-field information.
- Normalizing supported categories when confidence is sufficient.

Does not own:

- Calling Google Sheets.
- Sending Telegram messages.
- Deciding that a transaction should be persisted.
- Performing updates, deletes, or queries.
- Running background agent loops or invoking arbitrary tools.

### Google Sheets Repository

Owns:

- Appending validated transaction rows.
- Updating exactly one resolved transaction row.
- Looking up transactions for idempotency, update resolution, and queries.
- Mapping domain objects to the stable `Transactions` sheet contract in `integrations/google_sheets/schema.py`.
- Translating Google Sheets API failures into explicit repository errors.

Does not own:

- Natural-language parsing.
- Telegram reply formatting.
- Domain validation beyond defensive repository checks.

The repository implementation lives in `integrations/google_sheets/repository.py`.
Application services depend on `GoogleSheetsTransactionRepository` and its
`SheetsValuesClient` boundary rather than calling Google API resources directly.
The concrete `GoogleSheetsValuesClient` wraps the Sheets `spreadsheets().values()`
API for row reads, appends, and updates.

## Data Ownership

- Raw Telegram text is owned by the Telegram adapter until it is handed to the application service.
- Parsed intent is owned by the parser port as an untrusted proposal.
- Validated transaction state is owned by the application service and persisted through the repository.
- Google Sheets owns durable MVP storage after a write succeeds.

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
- Parser provider timeout or malformed parser response.
- Google Sheets API failure.
- Missing or invalid runtime configuration.

User-correctable errors should produce a clear Telegram reply and no storage mutation. System errors should produce a generic failure reply, preserve enough logs for debugging, and avoid duplicate writes on retry.

## Configuration

Required configuration:

- Telegram bot token.
- Parser provider credentials and model identifier.
- Google Sheets credentials.
- Google Sheet identifier and worksheet name.
- Default timezone.
- Default currency.

Secrets must come from environment variables or a secret manager. They must not be committed to the repository.

The Google Sheet must contain a worksheet named `Transactions` with the required header row described in `docs/google-sheets-template.md`.

## Testable Contracts

Future implementation should keep these contracts independently testable:

- Parser contract: raw text to parser result.
- Domain validation contract: parser result plus defaults to valid command or clarification.
- Repository contract: transaction append, update, lookup, and query behavior.
- Telegram adapter contract: Telegram update to metadata and reply call.
- Application service contract: orchestration across parser, validation, repository, and replies.

The Google Sheets repository contract is covered with an in-memory Sheets client
so duplicate lookup, latest lookup, update, monthly sum, schema validation, and
provider failure mapping can be tested without real credentials.
