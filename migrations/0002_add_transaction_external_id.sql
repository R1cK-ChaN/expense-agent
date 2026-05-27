alter table transactions
    add column external_id text;

update transactions
set external_id = id::text
where external_id is null;

alter table transactions
    alter column external_id set not null;

alter table transactions
    add constraint transactions_external_id_key unique (external_id);
