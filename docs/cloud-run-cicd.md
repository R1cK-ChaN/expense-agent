# Cloud Run CI/CD

GitHub Actions owns repeatable test and deployment gates for the Cloud Run
service. Pull requests run the Python test suite. Merges to `main` build the
Docker image with Cloud Build, push it to Artifact Registry, deploy it to Cloud
Run, and verify `/health`.

## Workflows

- `.github/workflows/ci.yml` runs `pytest` for pull requests and pushes to
  `main`.
- `.github/workflows/deploy-cloud-run.yml` runs on pushes to `main` and manual
  dispatch. It authenticates to Google Cloud with Workload Identity Federation
  and calls `scripts/deploy_cloud_run.sh`.

The deploy workflow must not use GitHub secrets for runtime application
secrets. Keep Telegram, parser, and Google credential values in GCP Secret
Manager and expose only secret names through repository or environment
variables.

## Required GitHub Variables

Configure these as repository variables or production environment variables:

| Variable | Purpose |
| --- | --- |
| `GCP_PROJECT_ID` | Google Cloud project ID. |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | Full Workload Identity Provider resource name. |
| `GCP_DEPLOY_SERVICE_ACCOUNT` | Deploy service account email used by GitHub Actions. |
| `CLOUD_RUN_REGION` | Cloud Run and Artifact Registry region, for example `asia-southeast1`. |
| `CLOUD_RUN_SERVICE` | Cloud Run service name, for example `expense-agent`. |
| `ARTIFACT_REGISTRY_REPOSITORY` | Docker Artifact Registry repository name. |
| `CLOUD_RUN_ENV_VARS` | Comma-separated non-secret runtime config passed to `--update-env-vars`. The legacy path requires `PARSER_MODEL`. The function path requires `FUNCTION_BATCHES_ENABLED=true`, `AGENT_MODEL=gpt-5.5`, and `STORAGE_BACKEND=postgres`. |
| `CLOUD_RUN_SECRET_MAPPINGS` | Comma-separated Secret Manager mappings passed to `--update-secrets`. Must include parser and IM-provider secrets plus `DATABASE_URL` for PostgreSQL or `GOOGLE_SERVICE_ACCOUNT_JSON` for the rollback backend. |

Optional variables:

| Variable | Purpose |
| --- | --- |
| `IMAGE_NAME` | Artifact Registry image name. Defaults to `expense-agent`. |
| `CLOUD_RUN_SERVICE_ACCOUNT` | Runtime service account for the Cloud Run revision. |
| `CLOUD_SQL_INSTANCE` | Cloud SQL connection name (`project:region:instance`). When set, the deploy script attaches its Auth socket to Cloud Run. |

Example non-secret config:

```text
SERVICE_NAME=expense-agent,DEFAULT_TIMEZONE=Asia/Singapore,DEFAULT_CURRENCY=SGD,PARSER_MODEL=gpt-4.1-mini,STORAGE_BACKEND=postgres
```

Staging function-batch example after migration `0004` is applied:

```text
SERVICE_NAME=expense-agent,DEFAULT_TIMEZONE=Asia/Singapore,DEFAULT_CURRENCY=SGD,FUNCTION_BATCHES_ENABLED=true,AGENT_MODEL=gpt-5.5,STORAGE_BACKEND=postgres
```

Keep `FUNCTION_BATCHES_ENABLED=false` in production until staging create,
multi-create, update, duplicate-delivery, clarification, and statistics smokes
pass. Disable it to return immediately to the legacy parser handler; the schema
and legacy duplicate queries remain compatible with batch-created rows.
When the variable is omitted, the deploy script explicitly writes
`FUNCTION_BATCHES_ENABLED=false` so Cloud Run cannot retain a prior enabled
value across rollback deployments.

Example Secret Manager mappings:

```text
TELEGRAM_BOT_TOKEN=telegram-bot-token:latest,TELEGRAM_WEBHOOK_SECRET=telegram-webhook-secret:latest,WECHAT_TOKEN=wechat-token:latest,PARSER_API_KEY=parser-api-key:latest,DATABASE_URL=database-url:latest
```

The deployment script validates credentials for the selected backend.
Production must retain its current setting until backfill verification and the
staging smoke path succeed; a successful deploy does not authorize cutover.

For Cloud SQL, store a percent-encoded Unix-socket URL in the `DATABASE_URL`
Secret Manager secret:

```text
postgresql://USER:PASSWORD@/DATABASE?host=/cloudsql/PROJECT:REGION:INSTANCE
```

Set `CLOUD_SQL_INSTANCE` to the matching connection name. The bot runtime and
projection runtime service accounts require `roles/cloudsql.client`; the
scheduler identity does not. The instance may use its connector-facing public
address without an authorized-network allowlist because the Cloud Run socket
uses the authenticated Cloud SQL connection path.

## GCP Setup

Create or reuse:

- An Artifact Registry Docker repository in `CLOUD_RUN_REGION`.
- A public Cloud Run service named by `CLOUD_RUN_SERVICE` so Telegram and
  WeChat can call the webhooks.
- Secret Manager secrets for the required runtime secret environment variables.
- A runtime service account with access to the required Secret Manager secrets.
- A deploy service account for GitHub Actions.

Grant the deploy service account the minimum roles needed to submit builds,
write images, deploy Cloud Run revisions, and act as the runtime service
account. Grant the GitHub Workload Identity principal access to impersonate the
deploy service account.

## Sheet Projection Schedule

The `Deploy Sheet Projection Schedule` workflow is manual and scoped to the
selected GitHub `staging` or `production` environment. It calls
`scripts/deploy_sheet_projection_job.sh`, which idempotently deploys a Cloud Run
Job running `sync_postgres_to_google_sheets.py` and creates or updates its Cloud
Scheduler HTTP trigger. It is intentionally separate from the bot deployment so
merging code does not enable production projection or production cutover.

Configure these variables in each GitHub environment:

| Variable | Purpose |
| --- | --- |
| `SHEET_PROJECTION_JOB` | Stable Cloud Run Job name. |
| `SHEET_PROJECTION_IMAGE_URI` | Already-built Expense Agent image containing `scripts/`. |
| `CLOUD_SQL_INSTANCE` | Cloud SQL connection name attached to the projection Job. |
| `CLOUD_RUN_SERVICE_ACCOUNT` | Existing bot runtime identity, used to reject credential reuse by the projection job. |
| `SHEET_PROJECTION_RUNTIME_SERVICE_ACCOUNT` | Dedicated job identity with access only to projection secrets and required APIs. |
| `SHEET_PROJECTION_SCHEDULER_SERVICE_ACCOUNT` | Separate identity permitted to invoke only the projection job. |
| `SHEET_PROJECTION_SECRET_MAPPINGS` | Must map `DATABASE_URL` and `GOOGLE_SERVICE_ACCOUNT_JSON` from environment-specific Secret Manager secrets. |
| `SHEET_PROJECTION_SCHEDULE` | Cron schedule; defaults to every five minutes. |
| `SHEET_PROJECTION_SCHEDULE_TIMEZONE` | Scheduler timezone; defaults to `Etc/UTC`. |

The deployment script enforces pairwise separation among the bot runtime,
projection job, and scheduler invocation identities. IAM grants remain
environment-owned prerequisites; the script does not
broaden IAM. Re-running the workflow updates the named job and schedule rather
than creating duplicates.

## Verification

The deploy script reads the deployed service URL from Cloud Run and runs:

```sh
curl --fail --silent --show-error --retry 5 --retry-delay 3 "$SERVICE_URL/health"
```

The current production health check target is:

```sh
curl https://expense-agent-fprjrwhbzq-as.a.run.app/health
```
