# Expense Agent

Telegram Expense Agent MVP.

Users send natural-language expense messages to a Telegram bot. The backend
parses, validates, stores transactions in the canonical Google Sheets ledger,
and replies with a confirmation. PostgreSQL is not selectable for bot runtime
traffic; its integration remains available only to offline migration,
verification, and export tooling.

Development follows the GitHub issues in this repository and uses TDD for implementation slices.

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

The bot uses Google Sheets as its canonical ledger. Configure
`GOOGLE_SERVICE_ACCOUNT_JSON` and `GOOGLE_SHEET_ID`; spending queries read the
`Transactions` worksheet without writing to it, while new expenses append one
row. PostgreSQL remains available only to migration, verification, and export
tooling. Google Sheets setup is documented in `docs/google-sheets-template.md`.
The offline Google Sheets to PostgreSQL backfill and verification tooling is
documented in `docs/postgres-backfill-cutover.md`; its former production
cutover procedure is retained only as historical context.

## Deployment

CI/CD for Cloud Run is defined in `.github/workflows/`. Pull requests run
`pytest`; merges to `main` build the Docker image, deploy to Cloud Run through
Workload Identity Federation, and verify `/health`. Setup details are in
`docs/cloud-run-cicd.md`.
