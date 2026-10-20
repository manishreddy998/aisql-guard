"""Config comes from aisql-guard.toml or a pyproject table."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from aisql_guard.config import ConfigError, load_config


def test_missing_file_uses_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    assert config.path is None
    assert config.dialect == "bigquery"
    assert config.fail_on == "error"
    assert config.default_model == "google-3.5-flash-lite"
    assert config.default_rows == 1000
    assert config.total_budget_usd is None
    assert config.snowflake_ai_credit_usd == Decimal("2.00")
    assert config.databricks_usd_per_dbu == Decimal("0.07")
    filled = config.assumption_for("missing_model")
    assert filled.rows == 1000
    assert filled.input_tokens == 500
    assert filled.model is None


def test_toml_file_fills_models_and_budgets(tmp_path: Path) -> None:
    path = tmp_path / "aisql-guard.toml"
    path.write_text(
        """
dialect = "snowflake"

[rules]
disable = ["ASG006"]
fail_on = "warning"

[estimate]
default_model = "llama3.1-8b"
default_rows = 10
snowflake_ai_credit_usd = "2.20"

[estimate.models.ticket_summaries_risky]
rows = 50000
input_tokens = 1500
output_tokens = 512
thinking_tokens = 2048
model = "google-3.5-flash"

[budgets]
total_usd = "50"

[budgets.models]
ticket_summaries_risky = "10.5"

[live]
project = "billing-lab"
location = "US"
sample_rows = 15

[live.models.ticket_summaries_risky]
prompt = "body"
source = "`warehouse.tickets`"
where = "status = 'open'"
""",
        encoding="utf-8",
    )
    nested = tmp_path / "models" / "marts"
    nested.mkdir(parents=True)
    config = load_config(nested / "model.sql")
    assert config.path == path.resolve()
    assert config.dialect == "snowflake"
    assert config.disabled_rules == frozenset({"ASG006"})
    assert config.fail_on == "warning"
    assert config.snowflake_ai_credit_usd == Decimal("2.20")
    risky = config.assumption_for("ticket_summaries_risky")
    assert risky.rows == 50000
    assert risky.thinking_tokens == 2048
    assert risky.model == "google-3.5-flash"
    other = config.assumption_for("other")
    assert other.rows == 10
    assert other.output_tokens == 128
    assert config.total_budget_usd == Decimal("50")
    assert config.model_budgets["ticket_summaries_risky"] == Decimal("10.5")
    sample = config.live_models["ticket_summaries_risky"]
    assert sample.prompt == "body"
    assert sample.where == "status = 'open'"
    assert config.live_project == "billing-lab"
    assert config.live_sample_rows == 15


def test_explicit_path_and_pyproject_section(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[tool.aisql-guard]
dialect = "databricks"

[tool.aisql-guard.budgets]
total_usd = "1"
""",
        encoding="utf-8",
    )
    from_section = load_config(tmp_path)
    assert from_section.dialect == "databricks"
    assert from_section.total_budget_usd == Decimal("1")
    assert from_section.path == pyproject.resolve()

    other = tmp_path / "other.toml"
    other.write_text('dialect = "bigquery"\n', encoding="utf-8")
    explicit = load_config(tmp_path, explicit=other)
    assert explicit.dialect == "bigquery"
    assert explicit.path == other.resolve()


def test_project_toml_wins_over_a_pyproject_table(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.aisql-guard]\ndialect = "databricks"\n',
        encoding="utf-8",
    )
    (tmp_path / "aisql-guard.toml").write_text('dialect = "snowflake"\n', encoding="utf-8")
    assert load_config(tmp_path).dialect == "snowflake"


def test_bad_values_are_config_errors(tmp_path: Path) -> None:
    broken = tmp_path / "broken.toml"
    broken.write_text("dialect = [\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="could not parse"):
        load_config(explicit=broken)

    missing = tmp_path / "missing.toml"
    with pytest.raises(ConfigError, match="not found"):
        load_config(explicit=missing)

    bad = tmp_path / "aisql-guard.toml"
    bad.write_text('[rules]\nfail_on = "fatal"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="fail_on"):
        load_config(tmp_path)

    bad.write_text("[estimate]\ndefault_rows = -1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="negative"):
        load_config(tmp_path)

    bad.write_text("[budgets]\ntotal_usd = true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="decimal"):
        load_config(tmp_path)

    bad.write_text("[rules]\ndisable = [1]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="disable"):
        load_config(tmp_path)

    bad.write_text("[estimate]\ndefault_rows = \"10\"\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="whole number"):
        load_config(tmp_path)

    bad.write_text("[budgets]\ntotal_usd = -1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="negative"):
        load_config(tmp_path)

    pyproject = tmp_path / "pyproject.toml"
    bad.unlink()
    pyproject.write_text("[tool]\naisql-guard = 'nope'\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a table"):
        load_config(tmp_path)
