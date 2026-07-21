# Domain Model

## Overview

The domain model separates user intent, parsed data, validation, source metadata, and persistence. Telegram and WeChat messages are provider envelopes, parser results describe the user's intent, domain validation decides whether the intent is safe to execute, and Google Sheets stores accepted transactions.

## IM Source Metadata

IM source metadata is captured for every handled message so the backend can reply, preserve audit context, and avoid duplicate writes across Telegram and WeChat.

Required fields:

- `source_platform`: provider identifier, currently `telegram` or `wechat`.
- `source_message_id`: provider message identifier within the conversation.
- `source_chat_id`: provider conversation or official-account identifier used for replies and duplicate detection.
- `source_user_id`: provider sender identifier.
- `message_text`: raw user-visible message text.
- `message_timestamp`: provider message timestamp converted to an ISO 8601 datetime.

Optional fields:

- `source_username`: provider username when available.
- `source_user_display_name`: provider display name when available.
- `reply_to_message_id`: message being replied to when available.
- `locale`: user or deployment locale when available.

Invariants:

- `source_platform`, `source_message_id`, `source_chat_id`, and `source_user_id` must be preserved exactly after provider-specific normalization.
- Telegram private `message_text` is handed to the parser unchanged.
- Group and supergroup `message_text` is handed to the parser only after the explicit bot mention is stripped by the Telegram adapter.
- WeChat Official Account text XML `Content` is handed to the parser unchanged after XML decoding.
- A transaction must retain enough metadata to detect duplicate processing of the same source platform/user/chat/message tuple.

## Transaction

A transaction is one stored expense row.

Required fields:

- `id`: backend-generated stable identifier.
- `date`: expense date as `YYYY-MM-DD` in the configured timezone.
- `amount`: positive decimal amount as provided by the user.
- `currency`: ISO 4217 currency code.
- `type`: transaction type, initially `expense`.
- `category`: one supported normalized category.
- `source_platform`: source provider identifier.
- `source_user_id`: source provider user identifier.
- `source_chat_id`: source provider conversation identifier.
- `source_message_id`: source provider message identifier.
- `created_at`: backend creation timestamp in ISO 8601 format.
- `updated_at`: backend update timestamp in ISO 8601 format.

Optional fields:

- `merchant`: merchant or place when confidently available.
- `payment_method`: card, wallet, cash, or other user-provided payment method when available.
- `note`: short human-readable description or extra details from the user.
- `source_username`: provider username when available.
- `source_user_display_name`: provider display name when available.

Invariants:

- `amount` must be greater than zero.
- `currency` must be one of the supported uppercase ISO 4217 currency codes.
- `type` must be `expense` for the MVP.
- `category` must be one of the supported category values.
- `date` must be a valid date.
- `id` must not change after creation.
- `created_at` must not change after creation.
- `updated_at` must change when a stored transaction is updated.
- Generated `created_at` and `updated_at` timestamps use the configured timezone and include an explicit offset, initially `Asia/Singapore` / `+08:00`.
- A source platform/user/chat/message tuple can create at most one transaction unless future requirements explicitly support multi-expense messages.

Create-expense validation returns a normalized transaction candidate before any
repository write is allowed:

- Missing `amount` fails with `这笔支出还缺金额，请补充一下。`.
- `amount` values of `0` or below fail.
- Missing `date` defaults to today's date in the configured timezone.
- Missing `currency` defaults to `SGD` unless runtime configuration supplies a different default.
- Supported currency aliases such as `人民币`, `RMB`, `美金`, and `新币` normalize to their canonical currency codes.
- Unsupported currency values fail validation before storage writes.
- Missing, blank, or unsupported `category` values become `未分类`.
- Missing `type` defaults to `expense`; any other type fails for the MVP.
- Messages that appear to contain multiple expense lines fail rather than creating multiple rows.

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

Expense total query fields:

- `start_date`: inclusive range start as `YYYY-MM-DD`.
- `end_date`: inclusive range end as `YYYY-MM-DD`.
- `currency`: reporting currency, or null to use the configured local currency.

Legacy parser responses containing `month` as `YYYY-MM` remain accepted and
are expanded to the full calendar month, except that the current month ends on
the requesting message's date so future-dated rows are not included.

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
- `requested_by_user_id`: source user requesting the update.
- `requested_at`: backend timestamp.

Supported update fields for the update-recent MVP:

- `date`
- `amount`
- `currency`
- `category`
- `merchant`
- `note`
- `payment_method`

`type`, arbitrary historical targeting, and multi-record updates are out of
scope until a future issue expands the update contract.

Invariants:

- The target must resolve to exactly one transaction before any write occurs.
- A user can update only transactions associated with the same source platform/user or chat policy defined by the implementation issue.
- Within one service process, duplicate provider deliveries for the same update
  or create-style retry-correction message reuse the transaction target chosen
  for the first successful update.
- Every changed field must satisfy the relevant transaction validation rules
  before storage is mutated.
- Unsupported parser-proposed update fields are ignored when at least one
  supported field can be safely applied; when no supported fields remain, the
  update fails with a user-facing unsupported-update reply.
- Updates must preserve the original `source_platform`, `source_user_id`, `source_chat_id`, `source_message_id`, user display metadata, and `created_at`.

## Correction Retry Guard

Before appending a newly parsed expense, the application service checks the
same user's latest recent expense for a likely currency correction retry.

The guard can update the latest row instead of appending when all stable fields
match and only currency differs:

- same source platform and user
- same transaction date
- same amount
- same category
- same normalized merchant or note
- latest row created within the configured short retry window

When stable fields match but the merchant/note is only similar, the service
returns a clarification prompt instead of appending a duplicate row. The guard
does not replace source-message idempotency.

## Query Request

A query request reads stored transactions and returns an answer without mutating storage.

Fields:

- `date_range`: start and end date, inclusive.
- `category`: optional normalized category filter.
- `limit`: optional maximum transaction count for list responses.
- `aggregation`: `total`, `list`, or `by_category`.
- `requested_by_user_id`: source user requesting the query.
- `requested_at`: backend timestamp.

Invariants:

- Query requests must not append, update, or delete transaction rows.
- Query date ranges must be bounded before storage access.
- Query results must be scoped to the requesting user or chat policy defined by the implementation issue.
- Total queries return the configured default-currency total for the requested
  inclusive date range; legacy current-month queries end on the request date.
- Non-default-currency expenses are converted with transaction-date daily reference rates for reporting only.
- When the exact transaction date has no rate, the latest previous available rate may be used and the rate date must be visible in the reply context.
- Original stored `amount` and `currency` must not be overwritten by report conversion.

## Supported Currencies

The supported mainstream currency enum is:

- `SGD`
- `CNY`
- `USD`
- `EUR`
- `GBP`
- `JPY`
- `HKD`
- `TWD`
- `MYR`
- `IDR`
- `THB`
- `VND`
- `KRW`
- `AUD`
- `NZD`
- `CAD`
- `CHF`
- `INR`
- `PHP`

Currency aliases are normalized before validation. Ambiguous symbols such as `$`
or `¥` are not treated as a specific foreign currency by the backend; when the
parser cannot resolve the intended code, validation falls back to the configured
default only if currency is omitted.

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
- `个人护理`
- `生活服务`
- `家庭`
- `服饰`
- `数码`
- `健身`
- `礼物`
- `税费`
- `保险`
- `其他`
- `未分类`

Category rules:

- Unknown but valid expenses use `未分类`.
- Unsupported category names must either normalize to a supported category or trigger clarification.
- Stored rows must never contain category synonyms.

The category allowlist is a core domain constant shared by parser and validator
code. The parser may preserve an unsupported create-expense category string so
the validator can safely fall back to `未分类`.

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
- `source_platform`
- `source_user_id`
- `source_username`
- `source_user_display_name`
- `source_chat_id`
- `source_message_id`
- `created_at`
- `updated_at`

The canonical worksheet name is `Transactions`. The code contract for the sheet name and header order lives in `integrations/google_sheets/schema.py`; repository implementations must import those constants instead of duplicating column names.

Repository implementations may add or rename columns only when future issues document the migration and tests cover backwards compatibility.
