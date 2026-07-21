# Project Handbook

This is the canonical entry point for Expense Agent documentation. The
handbook is the normative description of what the system should be; code and
tests are executable conformance evidence. Start with [Current State](now.md),
then follow only the documents relevant to the task.

## Fact Ownership

Each kind of fact has one owner:

| Fact | Owning document | Purpose |
| --- | --- | --- |
| Stable product and system behavior | [Requirements](requirements.md) | User-visible outcomes, defaults, scope, and acceptance cases. |
| Domain language and invariants | [Domain Model](domain-model.md) | Canonical terms, values, and domain rules. |
| Cross-boundary contracts | [Interfaces](interfaces.md) | Human-readable inputs, outputs, failure behavior, and links to detailed contracts. |
| Current boundaries and data flow | [Architecture](architecture.md) | Components, responsibilities, ownership, and runtime flow. |
| Persistent data shape | [Database Schema](database-schema.md) | Tables, constraints, indexes, and repository mapping. |
| Hard-to-reverse decisions | [Architecture Decisions](decisions/README.md) | Append-only decision history and consequences. |
| Current work and safe handoff | [Now](now.md) | Active work, blockers, open decisions, validation, and next actions. |
| Verification policy | [Testing Strategy](testing-strategy.md) | Test layers, TDD expectations, and documentation checks. |
| Deployment contract | [Cloud Run CI/CD](cloud-run-cicd.md) | Environment variables, identities, workflows, and health validation. |
| Operational migration | [PostgreSQL Cutover](postgres-backfill-cutover.md) | Backfill, verification, cutover, and rollback procedure. |
| Google workbook contract | [Google Sheets Template](google-sheets-template.md) | Rollback and projection worksheet schemas. |
| Telegram ingress operation | [Telegram Webhook](telegram-webhook.md) | Route configuration and smoke checks. |

## Fact Precedence

Documents do not silently override one another:

1. Accepted ADRs constrain hard-to-reverse intent until superseded by a later
   ADR.
2. Requirements own intended observable behavior within those decisions.
3. Interfaces own boundary contracts; architecture owns how responsibilities
   and data flow satisfy them.
4. Schema and runbooks own persistence and operational mechanics, respectively.
5. `now.md` records transitional reality and known gaps but cannot replace a
   stable requirement or accepted decision.
6. Code and tests show executable behavior. If they conflict with the handbook,
   record the discrepancy in `now.md` and resolve it explicitly; do not redefine
   intent by silently editing whichever artifact is most convenient.

## Maintenance Rules

- Change the owning document, implementation, and conformance tests together.
- Link to owned facts instead of copying them into multiple documents.
- Add an ADR for important, durable decisions; supersede rather than rewrite
  accepted history.
- Remove finished work from `now.md`; commit history and issues retain history.
- Keep credentials, private task metadata, and production data out of the
  handbook.
