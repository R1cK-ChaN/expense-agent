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

- `id`: backend-generated stable identifier.
- `date`: expense date as `YYYY-MM-DD` in the configured timezone.
- `amount`: positive decimal amount as provided by the user.
- `currency`: ISO 4217 currency code.
- `type`: transaction type, initially `expense`.
- `category`: one supported normalized category.
- `telegram_user_id`: source Telegram user identifier.
- `telegram_message_id`: source Telegram message identifier.
- `created_at`: backend creation timestamp in ISO 8601 format.
- `updated_at`: backend update timestamp in ISO 8601 format.

Optional fields:

- `merchant`: merchant or place when confidently available.
- `payment_method`: card, wallet, cash, or other user-provided payment method when available.
- `note`: short human-readable description or extra details from the user.

Invariants:

- `amount` must be greater than zero.
- `currency` must be an uppercase ISO 4217 code.
- `type` must be `expense` for the MVP.
- `category` must be one of the supported category values.
- `date` must be a valid date.
- `id` must not change after creation.
- `created_at` must not change after creation.
- `updated_at` must change when a stored transaction is updated.
- A Telegram user/message pair can create at most one transaction unless future requirements explicitly support multi-expense messages or multi-chat scoping.

## Parser Result

The parser result is a structured description of what the user likely asked for. It is not an execution plan and it never writes to storage.

Common fields:

- `intent`: one of `create_expense`, `update_recent_expense`, `query_monthly_total`, or `unknown`.
- `confidence`: number from `0` to `1`.
- `missing_fields`: fields required before execution can continue.
- `raw_text`: original message text.

Create expense fields:

- `amount`
- `currency`
- `note`
- `date`
- `type`
- `category`
- `merchant`
- `payment_method`

Update recent expense fields:

- `update_fields`: map of fields to replace on the user's latest expense after validation.

Monthly total query fields:

- `month`: explicit or inferred month as `YYYY-MM`.
- `currency`: currency code for the total, or null when omitted.

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

- `date`
- `type`
- `amount`
- `currency`
- `category`
- `merchant`
- `payment_method`
- `note`

Invariants:

- The target must resolve to exactly one transaction before any write occurs.
- A user can update only transactions associated with the same Telegram user or chat policy defined by the implementation issue.
- Every changed field must satisfy the same validation rules used for transaction creation.
- Updates must preserve the original `telegram_user_id`, `telegram_message_id`, and `created_at`.

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

- `餐饮`
- `交通`
- `购物`
- `住房`
- `订阅`
- `娱乐`
- `医疗`
- `教育`
- `办公`
- `旅行`
- `未分类`

Category rules:

- Unknown but valid expenses use `未分类`.
- Unsupported category names must either normalize to a supported category or trigger clarification.
- Stored rows must never contain category synonyms.

## Storage Row Shape

The Google Sheet should store one transaction per row with stable column names:

- `id`
- `date`
- `amount`
- `currency`
- `type`
- `category`
- `merchant`
- `payment_method`
- `note`
- `telegram_user_id`
- `telegram_message_id`
- `created_at`
- `updated_at`

The canonical worksheet name is `Transactions`. The code contract for the sheet name and header order lives in `integrations/google_sheets/schema.py`; repository implementations must import those constants instead of duplicating column names.

Repository implementations may add or rename columns only when future issues document the migration and tests cover backwards compatibility.
