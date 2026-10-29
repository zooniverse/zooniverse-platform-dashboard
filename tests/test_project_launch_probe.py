"""Static safety checks for the opt-in project-launch discovery probe."""

import runpy
from pathlib import Path


def test_project_probe_uses_only_aggregate_and_metadata_queries():
    script = Path(__file__).resolve().parents[1] / "scripts" / "probe_project_launches.py"
    queries = runpy.run_path(str(script))
    assert "information_schema.columns" in str(queries["SCHEMA_SQL"])
    for name in ("COVERAGE_SQL", "YEARLY_SQL", "RECENT_SQL"):
        sql = str(queries[name]).lower()
        assert "public.projects" in sql
        assert "count(*)" in sql
        assert "select *" not in sql
        assert all(
            restricted not in sql
            for restricted in ("email", "password", "owner_id", "user_id", "api_key")
        )
    assert "launch_approved is true" in str(queries["RECENT_SQL"]).lower()
    assert "launch_date::date" in str(queries["RECENT_SQL"]).lower()
