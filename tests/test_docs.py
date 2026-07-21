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


def test_postgres_backfill_runbook_documents_cutover_boundary():
    runbook = (ROOT / "docs" / "postgres-backfill-cutover.md").read_text()

    assert "backfill_google_sheets_to_postgres.py" in runbook
    assert "verify_postgres_backfill.py" in runbook
    assert "--execute" in runbook
    assert "STORAGE_BACKEND=postgres" in runbook
    assert "PostgreSQL is the authoritative ledger" in runbook
    assert "Production Cutover" in runbook
    assert "explicit approval" in runbook
    assert "Rollback" in runbook


def test_architecture_documents_database_to_sheets_export_projection():
    architecture = (ROOT / "docs" / "architecture.md").read_text()

    assert "Google Sheets Export Projection" in architecture
    assert "database -> Google Sheets" in architecture
    assert "google_sheet_exports" in architecture
    assert "sync_postgres_to_google_sheets.py" in architecture
    assert "`Ledger` worksheet" in architecture
    assert "deploy-sheet-projection.yml" in architecture


def test_sheet_template_keeps_rollback_and_projection_schemas_separate():
    template = (ROOT / "docs" / "google-sheets-template.md").read_text()

    assert "17-column" in template
    assert "11-column" in template
    assert "worksheet named `Ledger`" in template
    assert "Leave `Transactions`" in template
