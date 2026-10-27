"""Guards for the rule: never modify Lead Intel's tables or data."""

import re
from pathlib import Path
from types import SimpleNamespace

from app.db.engine import to_async_url
from app.db.lead_intel import lead_intel_metadata
from app.db.models import OUTREACH_SCHEMA, Base

ROOT = Path(__file__).resolve().parents[1]
LEAD_INTEL_TABLES = {t.name for t in lead_intel_metadata.sorted_tables} | {
    "runs", "run_grid_cells", "findings", "exclusions", "suppression_hashes", "merge_snapshots",
    "provider_calls", "outcomes", "page_audits", "page_checklists", "job_runs", "user_roles",
}  # fmt: skip


def test_our_models_live_only_in_outreach_schema() -> None:
    assert Base.metadata.tables, "expected at least one outreach table"
    for table in Base.metadata.tables.values():
        assert table.schema == OUTREACH_SCHEMA
        assert table.name not in LEAD_INTEL_TABLES
        assert not table.foreign_keys, f"{table.name} must not reference Lead Intel tables"


def test_lead_intel_tables_are_not_in_alembic_metadata() -> None:
    assert not set(Base.metadata.tables) & {f"public.{t}" for t in LEAD_INTEL_TABLES}


def test_migrations_only_touch_outreach_schema() -> None:
    for path in (ROOT / "migrations" / "versions").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for schema in re.findall(r"schema\s*=\s*['\"](\w+)['\"]", source):
            assert schema == OUTREACH_SCHEMA, f"{path.name} targets schema {schema}"
        for call in re.findall(r"op\.\w+\(\s*['\"](\w+)['\"]", source):
            assert call not in LEAD_INTEL_TABLES, f"{path.name} touches Lead Intel table {call}"
        assert "public." not in source, f"{path.name} mentions the public schema"


def test_lead_intel_repo_never_writes() -> None:
    source = (ROOT / "app" / "repositories" / "lead_intel.py").read_text(encoding="utf-8")
    for word in ("insert(", "update(", "delete(", ".commit(", "INSERT ", "UPDATE ", "DELETE ", "ALTER ", "DROP "):
        assert word not in source


def test_alembic_filters_out_everything_but_outreach() -> None:
    source = (ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
    # Load just the two filter functions, without running the migration at the bottom of env.py.
    namespace: dict = {"OUTREACH_SCHEMA": OUTREACH_SCHEMA}
    for name in ("include_name", "include_object"):
        block = re.search(rf"def {name}\(.*?(?=\n\n\n)", source, re.S).group(0)
        exec(block, namespace)

    assert namespace["include_name"]("outreach", "schema", {}) is True
    assert namespace["include_name"]("public", "schema", {}) is False
    assert namespace["include_name"](None, "schema", {}) is False
    public_table = SimpleNamespace(schema=None)
    assert namespace["include_object"](public_table, "businesses", "table", True, None) is False
    assert namespace["include_object"](SimpleNamespace(schema="public"), "icps", "table", True, None) is False
    assert namespace["include_object"](SimpleNamespace(schema="outreach"), "x", "table", False, None) is True


def test_supabase_url_is_converted_for_asyncpg() -> None:
    url, args = to_async_url("postgres://u:p@db.example.supabase.co:5432/postgres?sslmode=require")
    assert url == "postgresql+asyncpg://u:p@db.example.supabase.co:5432/postgres"
    assert args == {"ssl": "require"}
