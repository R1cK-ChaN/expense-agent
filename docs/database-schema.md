# Database Schema

## Purpose

This document describes a pragmatic PostgreSQL storage model for moving Expense
Agent beyond Google Sheets as the primary system of record.

The goal is to support:

- Durable transaction storage.
- Multi-IM user identity management.
- Reliable idempotency across Telegram and WeChat retries.
- Recent-state lookup for correction flows such as `change to CNY`.
- WeChat text, voice, location, and event message routing.
- Bounded historical context for reporting and LLM-assisted analysis.

The database owns durable state. The backend owns business rules. The LLM
parses user intent and may summarize bounded backend-selected context, but it
must not query the database directly or decide persistence.

## Design Principles

- Keep the schema close to third normal form.
- Separate human users from provider-specific identities.
- Separate inbound message metadata from expense transactions.
- Store normalized parser text separately from provider message type.
- Keep transaction rows as the current state.
- Store changes in an append-only event table when history matters.
- Prefer application-level category and currency validation before writes.
- Store location as auxiliary user context, not as a rule for currency or
  category decisions.
- Avoid generic key-value user profiles until concrete use cases require them.

## Core Tables

### users

One row per logical human user inside Expense Agent.

```sql
create table users (
    id uuid primary key,
    default_currency char(3) not null default 'SGD',
    timezone text not null default 'Asia/Singapore',
    language text not null default 'zh',
    last_latitude numeric(9, 6),
    last_longitude numeric(9, 6),
    last_location_updated_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
```

Notes:

- `default_currency`, `timezone`, and `language` are user preferences, not
  platform identity facts.
- `last_latitude`, `last_longitude`, and `last_location_updated_at` are
  auxiliary context from location messages or events. They must not override
  `default_currency`.
- New Telegram and WeChat users can initially create separate `users` rows.
  A later account-linking flow can merge identities under one user.

### user_identities

Maps Telegram, WeChat, or future IM identities to one internal user.

```sql
create table user_identities (
    id uuid primary key,
    user_id uuid not null references users(id),
    platform text not null,
    platform_user_id text not null,
    username text,
    display_name text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (platform, platform_user_id)
);
```

Notes:

- This table is the multi-IM bridge.
- `platform_user_id` is the sender identity from Telegram or WeChat.
- Chat or conversation IDs are not user identities. They belong to messages.

### inbound_messages

Stores provider delivery metadata and acts as the durable idempotency ledger.

```sql
create table inbound_messages (
    id uuid primary key,
    user_id uuid not null references users(id),
    identity_id uuid not null references user_identities(id),
    platform text not null,
    platform_chat_id text not null,
    platform_message_id text,
    provider_dedupe_key text not null,
    provider_message_type text not null,
    provider_event_type text,
    normalized_text text,
    received_at timestamptz not null,
    parser_intent text,
    parser_confidence numeric(4, 3),
    reply_text text,
    handled_at timestamptz,
    created_at timestamptz not null default now(),
    unique (platform, platform_chat_id, provider_dedupe_key),
    check (
        provider_message_type in (
            'text',
            'voice',
            'location',
            'event',
            'unsupported'
        )
    )
);
```

Notes:

- The unique constraint replaces row-scan duplicate detection in Google Sheets.
- `platform_message_id` stores provider IDs such as WeChat `MsgId` when present.
  Some WeChat events do not include a message ID, so `provider_dedupe_key` is the
  required idempotency key used by the application.
- For text messages, `normalized_text` is WeChat `Content` or Telegram text.
- For WeChat voice messages, `normalized_text` is `Recognition` when speech
  recognition succeeds.
- Location messages and events keep `normalized_text` null and must not enter
  the expense parser.
- If Telegram or WeChat retries the same webhook delivery, the service can
  return the stored `reply_text` instead of parsing or writing again.
- `parser_intent` and `parser_confidence` are processing facts for debugging and
  analytics. They are not used as trusted commands.

### WeChat message routing

The WeChat webhook should route supported message types before parser execution:

- `text`: copy `Content` into `normalized_text` and process it with the existing
  text parser.
- `voice`: copy `Recognition` into `normalized_text` and process it with the
  existing text parser.
- `voice` without `Recognition`: do not call the parser; reply with a short
  clarification such as `语音没识别清楚，可以发文字，例如：午饭 13。`.
- `location`: update the user's latest location fields and reply with a short
  acknowledgement.
- `event` with `Event = LOCATION`: update the user's latest location fields and
  return `success` without interrupting the user.
- `event` with `Event = subscribe`: return a welcome message.
- Other event or unsupported message types should not call the expense parser.

Voice and text share the same downstream parser, validation, and transaction
flow. Location is stored only as user context. It must not automatically change
`users.default_currency`, infer transaction currency, or infer category.

### transactions

Stores the current state of each accepted expense.

```sql
create table transactions (
    id uuid primary key,
    external_id text not null unique,
    user_id uuid not null references users(id),
    created_from_message_id uuid references inbound_messages(id),
    transaction_date date not null,
    amount numeric(18, 4) not null check (amount > 0),
    currency char(3) not null,
    transaction_type text not null default 'expense',
    category text not null,
    merchant text,
    payment_method text,
    note text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (created_from_message_id),
    check (transaction_type in ('expense'))
);
```

Notes:

- `id` is the internal database key. `external_id` preserves the existing
  domain-facing `TransactionRecord.id` value used by the application service
  and Google Sheets repository, including non-UUID values such as `txn-...`.
- `created_from_message_id` is nullable only to make legacy Google Sheets imports
  straightforward. New bot-created rows should always set it.
- Source platform, source user, chat, and message IDs are not duplicated here.
  They are available through `created_from_message_id`.
- `currency` and `category` stay as validated text in v1. Lookup tables can be
  added later if requirements need user-defined categories, translations, or
  richer category metadata.

### transaction_events

Append-only audit history for creates, corrections, and future deletes.

```sql
create table transaction_events (
    id uuid primary key,
    transaction_id uuid not null references transactions(id),
    message_id uuid references inbound_messages(id),
    event_type text not null,
    old_values jsonb,
    new_values jsonb,
    created_at timestamptz not null default now(),
    check (event_type in ('created', 'updated', 'corrected', 'deleted'))
);
```

Notes:

- `transactions` is the current state.
- `transaction_events` is the history.
- `old_values` and `new_values` are intentionally JSONB. This is not strict 3NF,
  but it is appropriate for audit logs because the main query model remains
  normalized.

## Indexes

```sql
create index idx_user_identities_user_id
    on user_identities(user_id);

create index idx_inbound_messages_user_received
    on inbound_messages(user_id, received_at desc);

create index idx_inbound_messages_user_type_received
    on inbound_messages(user_id, provider_message_type, received_at desc);

create index idx_transactions_user_created
    on transactions(user_id, created_at desc);

create index idx_transactions_user_date
    on transactions(user_id, transaction_date desc);

create index idx_transactions_user_month
    on transactions(user_id, transaction_date, currency);

create index idx_transaction_events_transaction
    on transaction_events(transaction_id, created_at desc);
```

These indexes cover the current product paths:

- Duplicate webhook delivery lookup.
- Latest expense lookup for `update_recent_expense`.
- WeChat text, voice, location, and event message inspection.
- Short-window duplicate correction checks.
- Monthly total and historical query flows.
- Transaction audit review.

## Repository Mapping

The existing application service already depends on a repository boundary. A
PostgreSQL repository should implement the same core behaviors first:

- `find_by_source_message`: read `inbound_messages` by unique provider tuple and
  join to any created transaction. For providers without stable message IDs,
  use `provider_dedupe_key`.
- `append_transaction`: insert `inbound_messages`, insert `transactions`, insert
  a `transaction_events` row, and store the reply text in one database
  transaction.
- `record_non_transaction_message`: insert or update `inbound_messages` for
  recognized voice failures, location messages, and events that should not
  create transactions.
- `update_user_location`: update `users.last_latitude`,
  `users.last_longitude`, and `users.last_location_updated_at` from WeChat
  `location` messages or `LOCATION` events.
- `get_latest_transaction`: query `transactions` by `user_id`, ordered by
  `created_at desc`.
- `update_transaction`: update the `transactions` row and insert a
  `transaction_events` row in one database transaction.
- `list_monthly_expenses`: query `transactions` by `user_id` and date range.

For the current codebase, the first implementation can keep the domain-facing
`TransactionRecord` shape and hide these joins inside the repository.

## LLM Context Boundary

PostgreSQL enables better historical features, but the LLM should receive only
bounded, backend-selected context.

Allowed examples:

- Last 5 expenses for the current user.
- This month's category totals.
- Recent transactions matching a merchant name.
- A compact summary of prior corrections for the same merchant.
- The user's latest location only when a future feature explicitly needs
  auxiliary context.

Not allowed:

- Letting the LLM generate arbitrary SQL.
- Giving the LLM direct database credentials.
- Passing unbounded transaction history into prompts.
- Using location as a hard rule for currency or category.
- Letting parser output bypass domain validation.

The backend should translate user intent into safe repository queries, then pass
only the minimal result set needed for response wording or classification.

## Migration From Google Sheets

Suggested migration path:

1. Create one `users` row per unique `(source_platform, source_user_id)` pair.
2. Create one `user_identities` row for each unique provider identity.
3. Import each Google Sheets row into `transactions`.
4. Leave `created_from_message_id` null for imported legacy rows if original raw
   message records are not available.
5. Create a `transaction_events` row with `event_type = 'created'` for each
   imported transaction.
6. Switch production writes to PostgreSQL.
7. Keep Google Sheets as an optional export if spreadsheet visibility is still
   useful.

## Deliberately Out Of Scope For V1

- Full double-entry accounting.
- Shared household or team accounts.
- Arbitrary custom category hierarchy.
- Merchant normalization tables.
- LLM-generated SQL.
- Event sourcing as the only source of truth.
- Hard deletion of financial records.

These can be added later if product requirements justify them. The v1 database
should solve durable user identity, idempotency, current transaction state, and
basic audit history first.
