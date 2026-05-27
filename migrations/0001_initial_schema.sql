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

create table transactions (
    id uuid primary key,
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
