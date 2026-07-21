# Current State

This file is a short-lived project handoff, not a changelog. It describes the
state that matters for the next safe action and should lose completed entries
as the project advances.

## Current State

- PR #53 implements Issue #52 and remains a draft pending final review and an
  explicit merge decision.
- PostgreSQL authority is accepted system intent, while production cutover has
  not been performed. The deployed production storage setting must not be
  inferred from merged or reviewed code.
- The implementation retains the 17-column `Transactions` worksheet as the
  temporary rollback backend and writes the derived 11-column `Ledger`
  projection from PostgreSQL.
- Projection deployment is an explicit environment-scoped Cloud Run Job and
  Cloud Scheduler action; an ordinary merge does not enable production
  scheduling or perform cutover.

## Active Work

- Issue #52: finish review and delivery of PostgreSQL-authoritative storage,
  migration, projection, and rollback support in PR #53.
- Issue #54: establish and verify this project handbook in the same PR at the
  repository owner's direction.

## Blockers

- No known implementation blocker is open.
- Production exposure remains intentionally blocked on the verification and
  approval steps in the [cutover runbook](postgres-backfill-cutover.md).

## Open Decisions

- Whether and when to approve production cutover after staging validation.
- When post-cutover evidence is sufficient to remove the temporary Google
  Sheets runtime rollback path; that cleanup is outside Issues #52 and #54.

## Validation State

- The handbook slice passes 293 tests, repository-local Markdown link checks,
  `git diff --check`, and a Codex review with no material findings.
- The preceding PR head passed GitHub Pytest and GitGuardian checks; CI must
  rerun for the handbook commit after it is pushed.
- No production cutover or production projection schedule has been executed as
  part of this work.

## Safe Next Actions

1. Verify handbook ownership, links, and the full test suite.
2. Review the complete PR diff and fix only material findings.
3. Push the documentation slice and update Issues #52 and #54 plus PR #53.
4. Keep the PR draft until the repository owner explicitly chooses readiness.
