# Domain Model

## Overview

The domain model separates user intent, parsed data, validation, and persistence. Telegram messages are the input envelope, parser results describe the user's intent, domain validation decides whether the intent is safe to execute, and Google Sheets stores accepted transactions.

## Telegram Message Metadata

Telegram metadata is captured for every handled message so the backend can reply, preserve audit context, and avoid duplicate writes.

Required fields:

- `update_id`: Telegram update identifier.
- `message_id`: Telegram message identifier within the chat.
- `chat_id`: Telegram chat identifier used for replies.
- `user_id`: Telegram sender identifier.
- `message_text`: raw user-visible message text.
- `message_timestamp`: Telegram message timestamp converted to an ISO 8601 datetime.

Optional fields:

- `username`: Telegram username when available.
- `first_name`: Telegram first name when available.
- `reply_to_message_id`: message being replied to when available.
- `locale`: user or deployment locale when available.

Invariants:

- `update_id`, `message_id`, `chat_id`, and `user_id` must be preserved exactly as received from Telegram.
- `message_text` must not be mutated before parser input; normalization belongs in parser or domain logic.
- A transaction created from Telegram must retain enough metadata to detect duplicate processing of the same Telegram message.

## Transaction

A transaction is one stored expense row.

Required fields:

- `transaction_id`: backend-generated stable identifier.
- `telegram_update_id`: source Telegram update identifier.
- `telegram_message_id`: source Telegram message identifier.
- `telegram_chat_id`: source Telegram chat identifier.
- `telegram_user_id`: source Telegram user identifier.
- `occurred_on`: expense date as `YYYY-MM-DD` in the configured timezone.
- `description`: short human-readable description.
- `amount`: positive decimal amount as provided by the user.
- `currency`: ISO 4217 currency code.
- `category`: one supported normalized category.
- `created_at`: backend creation timestamp in ISO 8601 format.
- `updated_at`: backend update timestamp in ISO 8601 format.

Optional fields:

- `merchant`: merchant or place when confidently available.
- `notes`: extra user-provided details that do not belong in normalized fields.
- `parser_confidence`: parser confidence score used for audit and debugging.

Invariants:

- `amount` must be greater than zero.
- `currency` must be an uppercase ISO 4217 code.
- `category` must be one of the supported category values.
- `occurred_on` must be a valid date.
- `description` must be non-empty after trimming whitespace.
- `transaction_id` must not change after creation.
- `created_at` must not change after creation.
- `updated_at` must change when a stored transaction is updated.
- A Telegram message can create at most one transaction unless future requirements explicitly support multi-expense messages.

## Parser Result

The parser result is a structured description of what the user likely asked for. It is not an execution plan and it never writes to storage.

Common fields:

- `intent`: one of `create_transaction`, `update_transaction`, `query_transactions`, or `unsupported`.
- `confidence`: number from `0` to `1`.
- `missing_fields`: fields required before execution can continue.
- `raw_text`: original message text.

Create transaction fields:

- `amount`
- `currency`
- `description`
- `occurred_on`
- `category`
- `merchant`
- `notes`

Update transaction fields:

- `target`: reference used to find the transaction, such as `last`, `previous`, date, category, amount, or description.
- `changes`: map of fields to replace after validation.

Query transaction fields:

- `date_range`: explicit or inferred start and end dates.
- `category`
- `limit`
- `aggregation`: `total`, `list`, or `by_category`.

Invariants:

- Parser output must preserve uncertainty through `confidence` and `missing_fields`.
- Parser output must use normalized category values when it provides a category.
- Parser output must not invent amounts, currencies, dates, or transaction targets.
- Parser output must not decide whether to persist, update, or reply. The backend owns those decisions.

## Update Request

An update request changes one existing transaction.

Fields:

- `target`: user-provided reference to the transaction.
- `candidate_transaction_ids`: transaction identifiers found during target resolution.
- `changes`: validated transaction fields to update.
- `requested_by_user_id`: Telegram user requesting the update.
- `requested_at`: backend timestamp.

Supported update fields:

- `occurred_on`
- `description`
- `amount`
- `currency`
- `category`
- `merchant`
- `notes`

Invariants:

- The target must resolve to exactly one transaction before any write occurs.
- A user can update only transactions associated with the same Telegram user or chat policy defined by the implementation issue.
- Every changed field must satisfy the same validation rules used for transaction creation.
- Updates must preserve the original Telegram source metadata and `created_at`.

## Query Request

A query request reads stored transactions and returns an answer without mutating storage.

Fields:

- `date_range`: start and end date, inclusive.
- `category`: optional normalized category filter.
- `limit`: optional maximum transaction count for list responses.
- `aggregation`: `total`, `list`, or `by_category`.
- `requested_by_user_id`: Telegram user requesting the query.
- `requested_at`: backend timestamp.

Invariants:

- Query requests must not append, update, or delete transaction rows.
- Query date ranges must be bounded before storage access.
- Query results must be scoped to the requesting user or chat policy defined by the implementation issue.
- Monetary totals must be grouped by currency unless exchange-rate support is explicitly added later.

## Supported Categories

The canonical category enum is:

- `food`
- `groceries`
- `transport`
- `housing`
- `utilities`
- `shopping`
- `health`
- `entertainment`
- `travel`
- `education`
- `work`
- `other`

Category rules:

- Unknown but valid expenses use `other`.
- Unsupported category names must either normalize to a supported category or trigger clarification.
- Stored rows must never contain category synonyms.

## Storage Row Shape

The Google Sheet should store one transaction per row with stable column names:

- `transaction_id`
- `telegram_update_id`
- `telegram_message_id`
- `telegram_chat_id`
- `telegram_user_id`
- `occurred_on`
- `description`
- `amount`
- `currency`
- `category`
- `merchant`
- `notes`
- `parser_confidence`
- `created_at`
- `updated_at`

Repository implementations may add internal columns only when future issues document the migration and tests cover backwards compatibility.
