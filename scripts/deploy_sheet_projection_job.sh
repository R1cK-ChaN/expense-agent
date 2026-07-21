#!/usr/bin/env bash
set -euo pipefail

required_environment=(
  GCP_PROJECT_ID
  CLOUD_RUN_REGION
  SHEET_PROJECTION_JOB
  SHEET_PROJECTION_IMAGE_URI
  BOT_RUNTIME_SERVICE_ACCOUNT
  SHEET_PROJECTION_RUNTIME_SERVICE_ACCOUNT
  SHEET_PROJECTION_SCHEDULER_SERVICE_ACCOUNT
  SHEET_PROJECTION_SECRET_MAPPINGS
)

for name in "${required_environment[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "::error::${name} is required"
    exit 1
  fi
done

if [[ "${SHEET_PROJECTION_RUNTIME_SERVICE_ACCOUNT}" == "${SHEET_PROJECTION_SCHEDULER_SERVICE_ACCOUNT}" ]]; then
  echo "::error::projection runtime and scheduler must use separate service accounts"
  exit 1
fi

if [[ "${SHEET_PROJECTION_RUNTIME_SERVICE_ACCOUNT}" == "${BOT_RUNTIME_SERVICE_ACCOUNT}" ]]; then
  echo "::error::projection job and production bot must use separate service accounts"
  exit 1
fi

has_mapping_key() {
  local mappings="$1"
  local expected_key="$2"
  local entry
  local key
  local value

  IFS="," read -ra entries <<< "${mappings}"
  for entry in "${entries[@]}"; do
    if [[ "${entry}" != *"="* ]]; then
      continue
    fi
    key="${entry%%=*}"
    value="${entry#*=}"
    if [[ "${key}" == "${expected_key}" && -n "${value}" ]]; then
      return 0
    fi
  done
  return 1
}

for key in DATABASE_URL GOOGLE_SERVICE_ACCOUNT_JSON; do
  if ! has_mapping_key "${SHEET_PROJECTION_SECRET_MAPPINGS}" "${key}"; then
    echo "::error::SHEET_PROJECTION_SECRET_MAPPINGS must include ${key}=..."
    exit 1
  fi
done

schedule_name="${SHEET_PROJECTION_SCHEDULE_NAME:-${SHEET_PROJECTION_JOB}-schedule}"
schedule="${SHEET_PROJECTION_SCHEDULE:-*/5 * * * *}"
schedule_timezone="${SHEET_PROJECTION_SCHEDULE_TIMEZONE:-Etc/UTC}"
default_timezone="${DEFAULT_TIMEZONE:-Asia/Singapore}"
job_uri="https://run.googleapis.com/v2/projects/${GCP_PROJECT_ID}/locations/${CLOUD_RUN_REGION}/jobs/${SHEET_PROJECTION_JOB}:run"

echo "Deploying projection job ${SHEET_PROJECTION_JOB}"
gcloud run jobs deploy "${SHEET_PROJECTION_JOB}" \
  --image="${SHEET_PROJECTION_IMAGE_URI}" \
  --project="${GCP_PROJECT_ID}" \
  --region="${CLOUD_RUN_REGION}" \
  --service-account="${SHEET_PROJECTION_RUNTIME_SERVICE_ACCOUNT}" \
  --command=python3 \
  --args=scripts/sync_postgres_to_google_sheets.py \
  --set-env-vars="DEFAULT_TIMEZONE=${default_timezone}" \
  --set-secrets="${SHEET_PROJECTION_SECRET_MAPPINGS}" \
  --max-retries=1 \
  --quiet

scheduler_args=(
  "${schedule_name}"
  "--project=${GCP_PROJECT_ID}"
  "--location=${CLOUD_RUN_REGION}"
  "--schedule=${schedule}"
  "--time-zone=${schedule_timezone}"
  "--uri=${job_uri}"
  "--http-method=POST"
  "--oauth-service-account-email=${SHEET_PROJECTION_SCHEDULER_SERVICE_ACCOUNT}"
  "--oauth-token-scope=https://www.googleapis.com/auth/cloud-platform"
  "--quiet"
)

if gcloud scheduler jobs describe "${schedule_name}" \
  --project="${GCP_PROJECT_ID}" \
  --location="${CLOUD_RUN_REGION}" >/dev/null 2>&1; then
  echo "Updating projection schedule ${schedule_name}"
  gcloud scheduler jobs update http "${scheduler_args[@]}"
else
  echo "Creating projection schedule ${schedule_name}"
  gcloud scheduler jobs create http "${scheduler_args[@]}"
fi
