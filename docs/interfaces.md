# Interfaces

This document owns the human-readable contracts at system boundaries. Detailed
domain values, persistence shapes, and operational procedures remain in their
owning documents linked below.

## Boundary Contracts

### Telegram and WeChat Ingress

- Input: provider-authenticated webhook payload plus stable source platform,
  user, chat, and message identifiers.
- Output: a provider-compatible reply or acknowledgement.
- Contract: duplicate provider deliveries cannot create duplicate transactions;
  invalid or ambiguous input cannot mutate the authoritative ledger.
- Detail: [Architecture](architecture.md#telegram-and-wechat-adapters),
  [Domain Model](domain-model.md#im-source-metadata), and
  [Telegram Webhook](telegram-webhook.md).

### Parser

- Input: user text plus explicit locale and configured defaults.
- Output: structured create, update, query, clarification, or unsupported intent.
- Contract: the parser proposes structured intent but never reads or writes the
  ledger and never decides persistence success.
- Detail: [Domain Model](domain-model.md#parser-result) and
  [Architecture](architecture.md#parser-port).

### Authoritative Ledger Repository

- Input: validated transaction commands and source-message identity.
- Output: committed transaction state, idempotency result, audit event, or a
  typed persistence failure.
- Contract: after approved cutover, PostgreSQL atomically owns inbound-message
  idempotency, transaction state, and append-only events. The Google Sheets
  repository is temporary migration and rollback compatibility only.
- Detail: [ADR 001](decisions/001-postgresql-ledger-ownership.md),
  [Database Schema](database-schema.md), and
  [Architecture](architecture.md#postgresql-repository).

### Spending Query Repository

- Input: one internal user and an inclusive date range.
- Output: matching transactions in their stored currencies.
- Contract: queries are read-only; conversion and category aggregation do not
  mutate transaction or projection state.
- Detail: [Requirements](requirements.md#query-stored-spending) and
  [Architecture](architecture.md#query-transactions).

### Google Sheets Projection

- Input: committed PostgreSQL transaction events after a per-user cursor.
- Output: idempotent upserts to the user's 11-column `Ledger` worksheet.
- Contract: projection failure cannot roll back a committed transaction or
  advance the cursor; Sheet edits never flow back into PostgreSQL.
- Detail: [Architecture](architecture.md#google-sheets-export-projection),
  [Google Sheets Template](google-sheets-template.md#postgresql-ledger-projection-sheet),
  and [Cutover Runbook](postgres-backfill-cutover.md).

### Exchange-Rate Provider

- Input: source currency, SGD target currency, and transaction date.
- Output: a positive deterministic reference rate and the actual rate date.
- Contract: failures produce an explicit reporting error rather than silently
  substituting an invented rate.
- Detail: [Requirements](requirements.md#supported-currencies) and
  [Architecture](architecture.md#exchange-rate-provider).

### Deployment and Scheduling

- Input: environment-owned GitHub variables, Workload Identity Federation, and
  Secret Manager mappings.
- Output: a Cloud Run service or an explicitly invoked projection Job/Scheduler
  update.
- Contract: deploy and production exposure are separate decisions; the bot,
  projection runtime, and scheduler identities are pairwise distinct.
- Detail: [Cloud Run CI/CD](cloud-run-cicd.md) and
  [Cutover Runbook](postgres-backfill-cutover.md#production-cutover).
