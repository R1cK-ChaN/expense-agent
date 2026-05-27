import os
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_repo_file(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_ci_workflow_runs_pytest_for_pull_requests():
    workflow = read_repo_file(".github/workflows/ci.yml")

    assert "pull_request:" in workflow
    assert "actions/setup-python" in workflow
    assert "python-version: \"3.12\"" in workflow
    assert "python -m pip install -e \".[dev]\"" in workflow
    assert "pytest" in workflow


def test_deploy_workflow_uses_workload_identity_and_not_github_secrets():
    workflow = read_repo_file(".github/workflows/deploy-cloud-run.yml")

    assert "branches: [main]" in workflow
    assert "needs: pytest" in workflow
    assert "id-token: write" in workflow
    assert "google-github-actions/auth" in workflow
    assert "workload_identity_provider: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}" in workflow
    assert "service_account: ${{ vars.GCP_DEPLOY_SERVICE_ACCOUNT }}" in workflow
    assert "scripts/deploy_cloud_run.sh" in workflow
    assert "secrets." not in workflow


def test_cloud_run_deploy_script_builds_deploys_and_checks_health():
    script = read_repo_file("scripts/deploy_cloud_run.sh")

    assert "gcloud builds submit" in script
    assert "--suppress-logs" in script
    assert "gcloud run deploy" in script
    assert "--update-secrets" in script
    assert "CLOUD_RUN_SECRET_MAPPINGS" in script
    assert "PARSER_MODEL" in script
    assert "GOOGLE_SHEET_ID" in script
    assert "STORAGE_BACKEND" in script
    assert "DATABASE_URL" in script
    assert "TELEGRAM_BOT_TOKEN" in script
    assert "WECHAT_TOKEN" in script
    assert "GOOGLE_SERVICE_ACCOUNT_JSON" in script
    assert "/health" in script
    assert "curl --fail" in script


def test_cloud_run_deploy_script_allows_postgres_backend_without_google_settings(
    tmp_path,
):
    install_fake_deploy_commands(tmp_path)
    result = run_deploy_script(
        tmp_path,
        {
            "CLOUD_RUN_ENV_VARS": (
                "PARSER_MODEL=gpt-4.1-mini,STORAGE_BACKEND=postgres"
            ),
            "CLOUD_RUN_SECRET_MAPPINGS": (
                "TELEGRAM_BOT_TOKEN=telegram-token:latest,"
                "TELEGRAM_WEBHOOK_SECRET=telegram-webhook-secret:latest,"
                "WECHAT_TOKEN=wechat-token:latest,"
                "PARSER_API_KEY=parser-api-key:latest,"
                "DATABASE_URL=database-url:latest"
            ),
        },
    )

    assert result.returncode == 0
    assert "::error::" not in result.stdout


def test_cloud_run_deploy_script_requires_database_url_for_postgres(tmp_path):
    install_fake_deploy_commands(tmp_path)
    result = run_deploy_script(
        tmp_path,
        {
            "CLOUD_RUN_ENV_VARS": (
                "PARSER_MODEL=gpt-4.1-mini,STORAGE_BACKEND=postgres"
            ),
            "CLOUD_RUN_SECRET_MAPPINGS": (
                "TELEGRAM_BOT_TOKEN=telegram-token:latest,"
                "TELEGRAM_WEBHOOK_SECRET=telegram-webhook-secret:latest,"
                "WECHAT_TOKEN=wechat-token:latest,"
                "PARSER_API_KEY=parser-api-key:latest"
            ),
        },
    )

    assert result.returncode == 1
    assert (
        "::error::CLOUD_RUN_SECRET_MAPPINGS must include DATABASE_URL=..."
        in result.stdout
    )


def test_dockerignore_excludes_local_secrets_and_build_noise():
    dockerignore = read_repo_file(".dockerignore")

    assert ".git" in dockerignore
    assert ".env" in dockerignore
    assert ".env.*" in dockerignore
    assert ".tools" in dockerignore
    assert ".pytest_cache" in dockerignore
    assert "__pycache__/" in dockerignore


def run_deploy_script(
    tmp_path: Path,
    overrides: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "GCP_PROJECT_ID": "project-id",
        "CLOUD_RUN_REGION": "asia-southeast1",
        "CLOUD_RUN_SERVICE": "expense-agent",
        "ARTIFACT_REGISTRY_REPOSITORY": "expense-agent",
        "CLOUD_RUN_ENV_VARS": (
            "PARSER_MODEL=gpt-4.1-mini,"
            "GOOGLE_SHEET_ID=sheet-id,"
            "STORAGE_BACKEND=google_sheets"
        ),
        "CLOUD_RUN_SECRET_MAPPINGS": (
            "TELEGRAM_BOT_TOKEN=telegram-token:latest,"
            "TELEGRAM_WEBHOOK_SECRET=telegram-webhook-secret:latest,"
            "WECHAT_TOKEN=wechat-token:latest,"
            "PARSER_API_KEY=parser-api-key:latest,"
            "GOOGLE_SERVICE_ACCOUNT_JSON=google-service-account-json:latest"
        ),
    }
    env.update(overrides)

    return subprocess.run(
        ["bash", "scripts/deploy_cloud_run.sh"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def install_fake_deploy_commands(tmp_path: Path) -> None:
    gcloud = tmp_path / "gcloud"
    gcloud.write_text(
        """#!/usr/bin/env bash
if [[ "$*" == *"run services describe"* ]]; then
  echo "https://expense-agent.example"
fi
exit 0
""",
        encoding="utf-8",
    )
    gcloud.chmod(0o755)

    curl = tmp_path / "curl"
    curl.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    curl.chmod(0o755)
