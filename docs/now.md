# Current State

This file is a short-lived project handoff, not a changelog. It describes the
state that matters for the next safe action and should lose completed entries
as the project advances.

## Current State

- PR #53 and Issues #52/#54 are merged and closed.
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

- Issue #55: add secure Cloud SQL attachment support, provision the approved
  cost-prioritized production database, verify backfill, and perform the
  explicitly approved PostgreSQL cutover.

## Blockers

- No known implementation blocker is open.
- Production exposure remains intentionally blocked on the verification and
  approval steps in the [cutover runbook](postgres-backfill-cutover.md).

## Open Decisions

- Production cutover is approved for Issue #55 only after its backfill and
  verification gates pass.
- When post-cutover evidence is sufficient to remove the temporary Google
  Sheets runtime rollback path; that cleanup is outside Issues #52 and #54.

## Validation State

- Merge commit `b710883` is deployed and healthy in production while retaining
  the Google Sheets backend.
- No production cutover or production projection schedule has yet been
  executed. Issue #55 must record fresh verification evidence before either.

## Safe Next Actions

1. Merge tested Cloud SQL attachment support without changing the active backend.
2. Provision the approved single-zone database and isolated runtime identities.
3. Run migration, dry-run backfill, executed backfill, and verification.
4. Cut over Cloud Run only after verification passes, then deploy and validate
   the projection schedule.
