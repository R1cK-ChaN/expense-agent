#!/usr/bin/env bash
set -euo pipefail

required_environment=(
  GCP_PROJECT_ID
  CLOUD_RUN_REGION
  CLOUD_RUN_SERVICE
  ARTIFACT_REGISTRY_REPOSITORY
)

for name in "${required_environment[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "::error::${name} is required"
    exit 1
  fi
done

require_mapping_keys() {
  local variable_name="$1"
  shift
  local mappings="${!variable_name:-}"

  if [[ -z "${mappings}" ]]; then
    echo "::error::${variable_name} is required"
    exit 1
  fi

  local key
  for key in "$@"; do
    if ! has_mapping_key "${mappings}" "${key}"; then
      echo "::error::${variable_name} must include ${key}=..."
      exit 1
    fi
  done
}

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

mapping_value() {
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
    if [[ "${key}" == "${expected_key}" ]]; then
      printf "%s" "${value}"
      return 0
    fi
  done

  return 1
}

require_mapping_keys CLOUD_RUN_ENV_VARS \
  PARSER_MODEL

require_mapping_keys CLOUD_RUN_SECRET_MAPPINGS \
  TELEGRAM_BOT_TOKEN \
  TELEGRAM_WEBHOOK_SECRET \
  WECHAT_TOKEN \
  PARSER_API_KEY

storage_backend="$(
  mapping_value "${CLOUD_RUN_ENV_VARS:-}" STORAGE_BACKEND || true
)"
storage_backend="${storage_backend:-google_sheets}"
storage_backend="${storage_backend,,}"

case "${storage_backend}" in
  google_sheets)
    require_mapping_keys CLOUD_RUN_ENV_VARS GOOGLE_SHEET_ID
    require_mapping_keys CLOUD_RUN_SECRET_MAPPINGS GOOGLE_SERVICE_ACCOUNT_JSON
    ;;
  postgres)
    require_mapping_keys CLOUD_RUN_SECRET_MAPPINGS DATABASE_URL
    ;;
  *)
    echo "::error::STORAGE_BACKEND must be google_sheets or postgres"
    exit 1
    ;;
esac

image_name="${IMAGE_NAME:-expense-agent}"
image_tag="${IMAGE_TAG:-${GITHUB_SHA:-manual}}"
image_uri="${CLOUD_RUN_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${ARTIFACT_REGISTRY_REPOSITORY}/${image_name}:${image_tag}"

echo "Building ${image_uri}"
gcloud builds submit --project "${GCP_PROJECT_ID}" --tag "${image_uri}" --suppress-logs .

deploy_args=(
  "${CLOUD_RUN_SERVICE}"
  "--image=${image_uri}"
  "--project=${GCP_PROJECT_ID}"
  "--region=${CLOUD_RUN_REGION}"
  "--platform=managed"
  "--allow-unauthenticated"
  "--quiet"
)

if [[ -n "${CLOUD_RUN_ENV_VARS:-}" ]]; then
  deploy_args+=("--update-env-vars=${CLOUD_RUN_ENV_VARS}")
fi

if [[ -n "${CLOUD_RUN_SECRET_MAPPINGS:-}" ]]; then
  deploy_args+=("--update-secrets=${CLOUD_RUN_SECRET_MAPPINGS}")
fi

if [[ -n "${CLOUD_RUN_SERVICE_ACCOUNT:-}" ]]; then
  deploy_args+=("--service-account=${CLOUD_RUN_SERVICE_ACCOUNT}")
fi

if [[ -n "${CLOUD_SQL_INSTANCE:-}" ]]; then
  deploy_args+=("--set-cloudsql-instances=${CLOUD_SQL_INSTANCE}")
fi

echo "Deploying ${CLOUD_RUN_SERVICE}"
gcloud run deploy "${deploy_args[@]}"

service_url="$(
  gcloud run services describe "${CLOUD_RUN_SERVICE}" \
    --project "${GCP_PROJECT_ID}" \
    --region "${CLOUD_RUN_REGION}" \
    --platform managed \
    --format "value(status.url)"
)"

if [[ -z "${service_url}" ]]; then
  echo "::error::Cloud Run service URL was not returned"
  exit 1
fi

echo "Checking ${service_url}/health"
curl --fail --silent --show-error --retry 5 --retry-delay 3 "${service_url}/health"
echo
