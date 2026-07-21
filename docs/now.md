# Current State

This file is a short-lived project handoff, not a changelog. It describes the
state that matters for the next safe action and should lose completed entries
as the project advances.

## Current State

- PR #53 and Issues #52/#54 are merged and closed.
- PostgreSQL is the deployed production source of truth. Cloud Run revision
  `expense-agent-00025-wzf` serves 100% of traffic with
  `STORAGE_BACKEND=postgres`, a Secret Manager `DATABASE_URL`, and an
  authenticated Cloud SQL attachment.
- Production uses a cost-prioritized PostgreSQL 16 Cloud SQL Enterprise
  `db-f1-micro` instance in `asia-southeast1-c`: single-zone, 10 GB SSD with
  automatic growth, automated backups, and deletion protection.
- The implementation retains the 17-column `Transactions` worksheet as the
  temporary rollback backend. PostgreSQL projects six user ledgers into the
  separate 11-column `Ledger` worksheet; Sheet edits do not flow upstream.
- The production `expense-agent-sheet-projection` Cloud Run Job runs every five
  minutes through an enabled Cloud Scheduler trigger. Bot, projection runtime,
  and scheduler identities are pairwise separate.

## Active Work

- Issue #55: persist final production cutover evidence and close delivery.

## Blockers

- No known implementation blocker is open.
- No known production or implementation blocker is open.

## Open Decisions

- When post-cutover evidence is sufficient to remove the temporary Google
  Sheets runtime rollback path; that cleanup is outside Issue #55.
- Whether future usage justifies upgrading the cost-prioritized single-zone
  Cloud SQL instance to regional high availability.

## Validation State

- Backfill imported 113 of 113 source transactions, and independent verification
  matched 113 Google Sheets rows to 113 PostgreSQL rows.
- PostgreSQL contains 113 transactions and 113 audit events. Six exports are
  enabled with zero errors and zero pending projection events.
- Cloud Run production health is passing after cutover.
- Manual and Scheduler-triggered projection Job executions both completed
  successfully; the Scheduler remains enabled on a five-minute cadence.
- Pull requests #56 and #57 passed CI, GitGuardian, and material-finding review.

## Safe Next Actions

1. Monitor the first normal production create, update, duplicate-delivery, and
   spending-query traffic against PostgreSQL and its audit events.
2. Keep the Google Sheets rollback credentials and unchanged `Transactions`
   worksheet until an explicit stabilization decision.
3. Investigate any projection `last_error` without moving the cursor manually;
   the next scheduled run retries pending events.
4. Reassess Cloud SQL availability and capacity as usage grows.
