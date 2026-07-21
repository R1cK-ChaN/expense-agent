create table function_call_batches (
    id uuid primary key,
    inbound_message_id uuid not null unique references inbound_messages(id),
    accepted_calls jsonb not null,
    operation_results jsonb,
    reply_text text,
    status text not null default 'accepted',
    created_at timestamptz not null default now(),
    completed_at timestamptz,
    check (jsonb_typeof(accepted_calls) = 'array'),
    check (
        operation_results is null
        or jsonb_typeof(operation_results) = 'array'
    ),
    check (status in ('selecting', 'accepted', 'writes_committed', 'completed', 'failed')),
    check (
        (status in ('selecting', 'accepted') and completed_at is null)
        or (status = 'writes_committed' and completed_at is null)
        or (status in ('completed', 'failed') and completed_at is not null)
    )
);

alter table transactions
    add column function_batch_id uuid references function_call_batches(id),
    add column function_call_index integer;

alter table transactions
    add constraint transactions_function_call_identity
        unique (function_batch_id, function_call_index),
    add constraint transactions_function_call_pair
        check (
            (function_batch_id is null and function_call_index is null)
            or (
                function_batch_id is not null
                and function_call_index is not null
                and function_call_index >= 0
            )
        );

create table function_call_executions (
    function_batch_id uuid not null references function_call_batches(id),
    function_call_index integer not null check (function_call_index >= 0),
    function_name text not null,
    status text not null,
    transaction_id uuid references transactions(id),
    result jsonb,
    created_at timestamptz not null default now(),
    completed_at timestamptz,
    primary key (function_batch_id, function_call_index),
    check (status in ('started', 'completed', 'failed'))
);

create table pending_requests (
    id uuid primary key,
    identity_id uuid not null references user_identities(id),
    platform_chat_id text not null,
    proposed_function text not null,
    known_arguments jsonb not null,
    missing_fields text[] not null,
    expires_at timestamptz not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (identity_id, platform_chat_id),
    check (jsonb_typeof(known_arguments) = 'object')
);

create index idx_function_call_batches_status_created
    on function_call_batches(status, created_at);

create index idx_pending_requests_expiry
    on pending_requests(expires_at);
