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
| `CLOUD_RUN_ENV_VARS` | Comma-separated non-secret runtime config passed to `--update-env-vars`. Must include `PARSER_MODEL` and `GOOGLE_SHEET_ID`. |
| `CLOUD_RUN_SECRET_MAPPINGS` | Comma-separated Secret Manager mappings passed to `--update-secrets`. Must include all required runtime secret environment variables. |

Optional variables:

| Variable | Purpose |
| --- | --- |
| `IMAGE_NAME` | Artifact Registry image name. Defaults to `expense-agent`. |
| `CLOUD_RUN_SERVICE_ACCOUNT` | Runtime service account for the Cloud Run revision. |

Example non-secret config:

```text
SERVICE_NAME=expense-agent,DEFAULT_TIMEZONE=Asia/Singapore,DEFAULT_CURRENCY=SGD,PARSER_MODEL=gpt-4.1-mini,GOOGLE_SHEET_ID=<sheet-id>,GOOGLE_WORKSHEET_NAME=Transactions
```

Example Secret Manager mappings:

```text
TELEGRAM_BOT_TOKEN=telegram-bot-token:latest,TELEGRAM_WEBHOOK_SECRET=telegram-webhook-secret:latest,WECHAT_TOKEN=wechat-token:latest,PARSER_API_KEY=parser-api-key:latest,GOOGLE_SERVICE_ACCOUNT_JSON=google-service-account-json:latest
```

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

## Verification

The deploy script reads the deployed service URL from Cloud Run and runs:

```sh
curl --fail --silent --show-error --retry 5 --retry-delay 3 "$SERVICE_URL/health"
```

The current production health check target is:

```sh
curl https://expense-agent-fprjrwhbzq-as.a.run.app/health
```
