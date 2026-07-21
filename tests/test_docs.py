import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_project_handbook_has_canonical_entry_points_and_fact_owners():
    readme = (ROOT / "README.md").read_text()
    index = (ROOT / "docs" / "index.md").read_text()
    now = (ROOT / "docs" / "now.md").read_text()
    interfaces = (ROOT / "docs" / "interfaces.md").read_text()

    assert "docs/index.md" in readme
    assert "Fact Ownership" in index
    assert "requirements.md" in index
    assert "interfaces.md" in index
    assert "architecture.md" in index
    assert "decisions/" in index
    assert "now.md" in index
    assert "Current State" in now
    assert "Active Work" in now
    assert "Blockers" in now
    assert "Safe Next Actions" in now
    assert "not a changelog" in now
    assert "Boundary Contracts" in interfaces


def test_architecture_decisions_are_indexed_and_append_only():
    decision_index = (ROOT / "docs" / "decisions" / "README.md").read_text()
    ledger_adr = (
        ROOT / "docs" / "decisions" / "001-postgresql-ledger-ownership.md"
    ).read_text()
    docs_adr = (
        ROOT / "docs" / "decisions" / "002-documentation-is-source.md"
    ).read_text()

    assert "append-only" in decision_index
    assert "001-postgresql-ledger-ownership.md" in decision_index
    assert "002-documentation-is-source.md" in decision_index
    assert "Status: Accepted" in ledger_adr
    assert "PostgreSQL" in ledger_adr
    assert "Status: Accepted" in docs_adr
    assert "normative system definition" in docs_adr
    assert "executable conformance evidence" in docs_adr


def test_repository_local_markdown_links_resolve():
    missing_links = []
    markdown_files = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]

    for source in markdown_files:
        text = source.read_text()
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_text = target.split("#", 1)[0]
            if not path_text:
                continue
            destination = (source.parent / path_text).resolve()
            if not destination.exists():
                missing_links.append(f"{source.relative_to(ROOT)} -> {target}")

    assert missing_links == []


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
