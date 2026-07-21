# ADR 001: PostgreSQL Owns the Authoritative Ledger

Status: Accepted

## Context

Google Sheets is valuable for user visibility, but manual edits, schema drift,
API availability, and query limits make it unsuitable as durable transaction
authority. The repository already has atomic PostgreSQL writes, append-only
transaction events, idempotent backfill, verification, and a one-way Sheet
projection.

## Decision

PostgreSQL owns transaction state, provider-message idempotency, and audit
events after cutover. Google Sheets is a replaceable projection derived from
committed transaction events. Sheet edits never flow back into PostgreSQL.

`STORAGE_BACKEND` temporarily preserves the Google Sheets runtime path for
migration and rollback. Production remains on its current setting until an
explicit verification and cutover approval; staging uses PostgreSQL first.

## Consequences

- A successful expense does not depend on Google Sheets availability.
- Projection failures retain their cursor and error evidence for retry.
- Backfill is one-time and dry-run-first; continuous bidirectional sync is
  forbidden.
- Rollback to the legacy Sheet runtime requires reconciling PostgreSQL-only
  writes created during the cutover window.
