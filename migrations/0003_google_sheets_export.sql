create table google_sheet_exports (
    user_id uuid primary key references users(id),
    spreadsheet_id text not null,
    enabled boolean not null default true,
    last_synced_event_id uuid references transaction_events(id),
    last_synced_at timestamptz,
    last_error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index idx_google_sheet_exports_enabled
    on google_sheet_exports(enabled)
    where enabled = true;
