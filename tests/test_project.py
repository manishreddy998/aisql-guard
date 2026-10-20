"""The example dbt project is the fixture for good and bad models."""

from __future__ import annotations

from pathlib import Path

from aisql_guard.engine import lint_paths, lint_sql

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "examples" / "support_tickets"

EXPECTED = {
    "models/staging/stg_tickets.sql": [],
    "models/staging/raw_summary_view.sql": ["ASG005"],
    "models/marts/capped_batch.sql": [],
    "models/marts/incremental_safe.sql": [],
    "models/marts/uncapped_generate.sql": ["ASG001", "ASG002"],
    "models/marts/no_thinking.sql": ["ASG002"],
    "models/marts/outer_limit.sql": ["ASG003"],
    "models/marts/prompt_limit.sql": ["ASG003"],
    "models/marts/incremental_rerun.sql": ["ASG004"],
    "models/marts/billing_ephemeral.sql": ["ASG005"],
    "models/marts/full_table_score.sql": ["ASG006"],
    "models/marts/star_prompt.sql": ["ASG007"],
    "models/marts/ticket_summaries_risky.sql": [
        "ASG001",
        "ASG002",
        "ASG006",
        "ASG007",
        "ASG003",
    ],
}


def test_example_project_matches_the_rule_map() -> None:
    result = lint_paths([PROJECT], dialect="bigquery")
    found: dict[str, list[str]] = {path: [] for path in EXPECTED}
    for finding in result.findings:
        found.setdefault(finding.path, []).append(finding.rule_id)
    assert found == EXPECTED


def test_schema_yml_can_force_a_view(tmp_path: Path) -> None:
    project = tmp_path / "dbt_project.yml"
    project.write_text(
        "name: demo\nmodel-paths: ['models']\nmodels:\n  demo:\n    +materialized: table\n",
        encoding="utf-8",
    )
    models = tmp_path / "models"
    models.mkdir()
    (models / "schema.yml").write_text(
        "version: 2\nmodels:\n  - name: scored\n    config:\n      materialized: view\n",
        encoding="utf-8",
    )
    (models / "scored.sql").write_text(
        "select AI.SCORE(('rate ', body), "
        "model_params => JSON "
        '\'{"generation_config":{"thinking_config":{"thinking_budget":0}}}\') '
        "from tickets where id = 1\n",
        encoding="utf-8",
    )
    result = lint_paths([tmp_path])
    assert [item.rule_id for item in result.findings] == ["ASG005"]


def test_dialect_examples() -> None:
    databricks = lint_sql(
        (ROOT / "examples" / "dialects" / "databricks_uncapped.sql").read_text(encoding="utf-8"),
        dialect="databricks",
        materialization="table",
    )
    assert [item.rule_id for item in databricks.findings] == ["ASG001"]
    snowflake = lint_sql(
        (ROOT / "examples" / "dialects" / "snowflake_capped.sql").read_text(encoding="utf-8"),
        dialect="snowflake",
        materialization="table",
    )
    assert snowflake.findings == []
