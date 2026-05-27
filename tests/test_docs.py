from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_architecture_documents_multi_im_boundary():
    architecture = (ROOT / "docs" / "architecture.md").read_text()

    assert "WeChat" in architecture
    assert "source_platform" in architecture
    assert "Telegram and WeChat adapters" in architecture


def test_domain_model_documents_generic_source_metadata():
    domain_model = (ROOT / "docs" / "domain-model.md").read_text()

    assert "## IM Source Metadata" in domain_model
    assert "`source_platform`" in domain_model
    assert "`source_user_id`" in domain_model


def test_postgres_backfill_cutover_runbook_documents_gates():
    runbook = (ROOT / "docs" / "postgres-backfill-cutover.md").read_text()

    assert "backfill_google_sheets_to_postgres.py" in runbook
    assert "verify_postgres_backfill.py" in runbook
    assert "--execute" in runbook
    assert "STORAGE_BACKEND=postgres" in runbook
    assert "STORAGE_BACKEND=google_sheets" in runbook
    assert "read-only" in runbook
