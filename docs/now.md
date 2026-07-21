# Current State

This file is a short-lived project handoff, not a changelog. It describes the
state that matters for the next safe action and should lose completed entries
as the project advances.

## Current State

- PRs #60–#62 and Issue #59 are merged and closed. Issue #63 corrects group
  statistics scope after production diagnosis found personal-only reads.
- PostgreSQL is the deployed production source of truth. Cloud Run revision
  `expense-agent-00031-2b5` serves 100% of traffic with
  `STORAGE_BACKEND=postgres`, `FUNCTION_BATCHES_ENABLED=true`,
  `AGENT_MODEL=gpt-5.5`, a Secret Manager `DATABASE_URL`, and an authenticated
  Cloud SQL attachment.
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

- Issue #63 implements deterministic personal/private and conversation/group
  statistics scope without changing write ownership or adding a migration.

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
- Issue #59 and follow-up PR #61 pass 348 tests, CI, GitGuardian, and material
  review. Migration `0004` completed through the one-time production Cloud Run
  Job execution `expense-agent-migrate-0004-cw7xn`; the Job configuration was
  removed afterward while execution logs remain available.
- Production revision `expense-agent-00031-2b5` started cleanly, passed the
  deployment and independent `/health` checks, and has no startup errors.
- Production diagnosis for Issue #63 confirmed the July query had correct dates
  and filters but read only the requester; the same chat contained the expected
  cross-member transactions. No production ledger rows were modified.

## Safe Next Actions

1. Complete Issue #63, deploy through the existing production workflow, and
   repeat the real Telegram group July-to-date query. Confirm the result uses
   conversation scope while an explicit personal query remains personal.
2. Keep the Google Sheets rollback credentials and unchanged `Transactions`
   worksheet until an explicit stabilization decision.
3. Investigate any projection `last_error` without moving the cursor manually;
   the next scheduled run retries pending events.
4. Reassess Cloud SQL availability and capacity as usage grows.
