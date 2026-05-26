from pathlib import Path


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
    assert "gcloud run deploy" in script
    assert "--update-secrets" in script
    assert "CLOUD_RUN_SECRET_MAPPINGS" in script
    assert "PARSER_MODEL" in script
    assert "GOOGLE_SHEET_ID" in script
    assert "TELEGRAM_BOT_TOKEN" in script
    assert "GOOGLE_SERVICE_ACCOUNT_JSON" in script
    assert "/health" in script
    assert "curl --fail" in script


def test_dockerignore_excludes_local_secrets_and_build_noise():
    dockerignore = read_repo_file(".dockerignore")

    assert ".git" in dockerignore
    assert ".env" in dockerignore
    assert ".env.*" in dockerignore
    assert ".tools" in dockerignore
    assert ".pytest_cache" in dockerignore
    assert "__pycache__/" in dockerignore
