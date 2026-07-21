# Expense Agent

Telegram Expense Agent MVP.

Users send natural-language expense messages to a Telegram bot. The backend
parses, validates, stores transactions in an authoritative ledger, and replies
with a confirmation. PostgreSQL is the authoritative ledger target;
Google Sheets remains available as the temporary rollback backend and as a
replaceable user-visible projection during cutover.

Development follows the GitHub issues in this repository and uses TDD for implementation slices.

The project handbook starts at [`docs/index.md`](docs/index.md). It identifies
which document owns requirements, interfaces, architecture, decisions, current
work, and operational procedures.

## Local Development

Create a virtual environment and install the project with test dependencies:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

Run the test suite:

```sh
pytest
```

Run the local FastAPI service:

```sh
uvicorn app.main:app --reload
```

Copy `.env.example` for local configuration. The health endpoint imports and
runs without real external credentials.

Set `STORAGE_BACKEND=postgres` with `DATABASE_URL` to make runtime reads and
writes use PostgreSQL. Keep `STORAGE_BACKEND=google_sheets` with
`GOOGLE_SERVICE_ACCOUNT_JSON` and `GOOGLE_SHEET_ID` only for the pre-cutover or
rollback runtime path. PostgreSQL-to-Sheets projection writes the separate
`Ledger` worksheet through `scripts/sync_postgres_to_google_sheets.py`; Sheet
edits are never imported as runtime commands. The Cloud Run Job and Cloud
Scheduler deployment entrypoint is `scripts/deploy_sheet_projection_job.sh`.
Google Sheets setup is documented in
`docs/google-sheets-template.md`, and the backfill, verification, cutover, and
rollback procedure is in `docs/postgres-backfill-cutover.md`.

## Deployment

CI/CD for Cloud Run is defined in `.github/workflows/`. Pull requests run
`pytest`; merges to `main` build the Docker image, deploy to Cloud Run through
Workload Identity Federation, and verify `/health`. Setup details are in
`docs/cloud-run-cicd.md`.
