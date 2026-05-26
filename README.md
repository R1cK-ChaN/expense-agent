# Expense Agent

Telegram Expense Agent MVP.

Users send natural-language expense messages to a Telegram bot. The backend parses, validates, stores transactions in Google Sheets, and replies with a confirmation.

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

Copy `.env.example` for local configuration. The health endpoint imports and runs without real external credentials.

Google Sheets setup for the MVP storage template is documented in `docs/google-sheets-template.md`.

## Deployment

CI/CD for Cloud Run is defined in `.github/workflows/`. Pull requests run
`pytest`; merges to `main` build the Docker image, deploy to Cloud Run through
Workload Identity Federation, and verify `/health`. Setup details are in
`docs/cloud-run-cicd.md`.
