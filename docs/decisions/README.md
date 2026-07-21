# Architecture Decision Records

This directory is the append-only history of important, hard-to-reverse system
decisions. Accepted ADRs are not rewritten to match later preferences. A new ADR
must supersede an older one and link both directions when a decision changes.

Every ADR records a stable number, title, status, context, decision, and
consequences. Proposed decisions may change before acceptance; accepted or
superseded decisions retain their historical wording except for explicit status
and supersession links.

## Decision Index

| ADR | Status | Decision |
| --- | --- | --- |
| [001](001-postgresql-ledger-ownership.md) | Accepted | PostgreSQL owns the authoritative ledger; Google Sheets is a projection and temporary rollback path. |
| [002](002-documentation-is-source.md) | Accepted | The handbook is the normative system definition; code and tests are executable conformance evidence. |
| [003](003-deterministic-function-batches.md) | Accepted | The LLM selects one function batch; deterministic backend code owns validation, ledger access, statistics, and replies. |
