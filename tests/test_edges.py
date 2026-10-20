"""Edges around Jinja, dialects, and dbt project lookup."""

from __future__ import annotations

from pathlib import Path

from aisql_guard import __version__, lint_paths
from aisql_guard.analyze import position
from aisql_guard.catalog import get_spec
from aisql_guard.discover import find_dbt_project, iter_sql_files, relative_path
from aisql_guard.engine import lint_sql
from aisql_guard.jinja_sql import render_for_parse

CAPPED = (
    "model_params => JSON "
    '\'{"generation_config":{"max_output_tokens":8,'
    '"thinking_config":{"thinking_budget":0}}}\''
)


def test_one_sql_file_can_be_linted_on_its_own() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "support_tickets"
        / "models"
        / "marts"
        / "capped_batch.sql"
    )
    result = lint_paths([path])
    assert result.findings == []
    assert result.calls[0].name == "AI.GENERATE"


def test_version_and_catalog_aliases() -> None:
    assert __version__ == "0.1.0"
    assert get_spec("GENERATE_TEXT") is not None
    assert get_spec("no-such-function") is None
    generate = get_spec("AI.GENERATE")
    assert generate is not None
    assert generate.needs_thinking_budget


def test_position_clamps_offsets() -> None:
    assert position("ab\nc", -5) == (1, 1)
    assert position("ab\nc", 50)[0] == 2


def test_endpoint_and_remote_model_hints() -> None:
    sql = f"""
    select AI.GENERATE(
      body,
      endpoint => 'flash-lite',
      {CAPPED}
    ).result
    from tickets
    where id = 1
    """
    call = lint_sql(sql, materialization="table").calls[0]
    assert call.model_hint == "flash-lite"
    assert call.output_token_cap == 8
    remote = """
    select *
    from ML.GENERATE_TEXT(
      model `warehouse.remote_models.ticket_model`,
      (select body as prompt from tickets where id = 1),
      struct(
        8 as max_output_tokens,
        '{"generation_config":{"thinking_config":{"thinking_budget":0}}}' as model_params
      )
    )
    """
    assert lint_sql(remote, materialization="table").calls[0].model_hint.endswith("ticket_model")


def test_finding_dict_and_duplicate_reports_collapse() -> None:
    sql = """
    select AI.GENERATE(body).result as summary
    from tickets
    where id = 1
    {% if is_incremental() %}
      and updated_at > '2026-01-01'
    {% endif %}
    """
    result = lint_sql(sql, materialization="table")
    assert [item.rule_id for item in result.findings] == ["ASG001", "ASG002"]
    assert result.findings[0].to_dict()["rule_id"] == "ASG001"


def test_default_dbt_materialization_is_a_view(tmp_path: Path) -> None:
    (tmp_path / "dbt_project.yml").write_text(
        "name: demo\nmodel-paths: ['models']\n",
        encoding="utf-8",
    )
    models = tmp_path / "models"
    models.mkdir()
    (models / "scored.sql").write_text(
        "select AI.SCORE(('rate ', body), "
        "model_params => JSON "
        '\'{"generation_config":{"thinking_config":{"thinking_budget":0}}}\') '
        "from tickets where id = 1\n",
        encoding="utf-8",
    )
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "compiled.sql").write_text(
        "select AI.GENERATE(body).result from tickets\n",
        encoding="utf-8",
    )
    result = lint_paths([tmp_path])
    assert [item.rule_id for item in result.findings] == ["ASG005"]
    assert iter_sql_files(tmp_path / "notes.txt") == []


def test_broken_schema_yml_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "dbt_project.yml").write_text(
        "name: demo\nmodel-paths: 'models'\nmodels: []\n",
        encoding="utf-8",
    )
    models = tmp_path / "models"
    models.mkdir()
    (models / "schema.yml").write_text("models: [\n", encoding="utf-8")
    (models / "plain.sql").write_text("select 1 as id\n", encoding="utf-8")
    assert lint_paths([tmp_path]).findings == []
    assert find_dbt_project(tmp_path / "models" / "plain.sql") is not None
    assert find_dbt_project(Path("/")) is None
    assert relative_path(Path("/etc/hostname"), tmp_path) == "/etc/hostname"


def test_anti_join_and_except_skip_generated_rows() -> None:
    anti = f"""
    select AI.GENERATE(body, {CAPPED}).result as summary
    from src
    anti join {{{{ this }}}} as already
      on already.ticket_id = src.ticket_id
    """
    assert lint_sql(anti, dialect="databricks", materialization="incremental").findings == []
    except_sql = f"""
    select ticket_id, AI.GENERATE(body, {CAPPED}).result as summary
    from src
    where status = 'closed'
    except distinct
    select ticket_id, summary from {{{{ this }}}}
    """
    assert lint_sql(except_sql, materialization="incremental").findings == []


def test_offset_and_other_functions() -> None:
    offset = f"""
    select AI.GENERATE(body, {CAPPED}).result
    from tickets
    where id = 1
    limit 10 offset 5
    """
    assert [item.rule_id for item in lint_sql(offset, materialization="table").findings] == [
        "ASG003"
    ]
    agg = "select AI.AGG(body, 'summarize the tickets') as note from tickets where id = 1"
    assert lint_sql(agg, materialization="table").findings[0].rule_id == "ASG001"
    cortex = """
    select SNOWFLAKE.CORTEX.COMPLETE('llama3.1-8b', body) as summary
    from tickets
    where id = 1
    """
    result = lint_sql(cortex, dialect="snowflake", materialization="table")
    assert [item.rule_id for item in result.findings] == ["ASG001"]
    assert result.calls[0].model_hint == "llama3.1-8b"
    boolean = f"""
    select AI.GENERATE_BOOL(body, {CAPPED}).result
    from tickets
    where id = 1
    """
    assert lint_sql(boolean, materialization="table").findings == []


def test_jinja_comments_negation_and_placeholders() -> None:
    rendered = render_for_parse(
        "{# note #}\n{% if not is_incremental() %}\nselect full\n{% else %}\nselect rerun\n{% endif %}\n",
        incremental=True,
    )
    assert "rerun" in rendered
    assert "full" not in rendered
    other = render_for_parse(
        "{% if var('flag') %}\nselect kept\n{% elif true %}\nselect dropped\n{% endif %}\n{% else %}\n",
        incremental=True,
    )
    assert "kept" in other
    assert "dropped" not in other
    names = render_for_parse(
        "select {{ ref('1tickets') }}, {{ var('x') }}, {{ config(materialized='table') }}",
        incremental=True,
    )
    assert "m_1tickets" in names
    assert "NULL" in names
    assert "materialized" not in names


def test_comments_strings_and_unparsed_limit() -> None:
    sql = f"""
    -- AI.GENERATE(body)
    select AI.GENERATE(
      concat('AI.GENERATE is not a second call. LIMIT stays in this string. ', body),
      {CAPPED}
    ).result
    from tickets
    where id = 1
    """
    assert lint_sql(sql, materialization="table").findings == []
    broken = "select AI.GENERATE((select * from tickets limit 3) from"
    ids = [item.rule_id for item in lint_sql(broken, materialization="table").findings]
    assert "ASG000" in ids
    assert "ASG003" in ids
    assert "ASG007" in ids


def test_triple_quoted_model_params_and_materialized_view() -> None:
    sql = """
    select *
    from ML.GENERATE_TEXT(
      model `warehouse.remote_models.ticket_model`,
      (select body as prompt from tickets where id = 1),
      struct('''
        {"generation_config":{"max_output_tokens":40,"thinking_config":{"thinking_budget":0}}}
      ''' as model_params)
    )
    """
    assert lint_sql(sql, materialization="table").findings == []
    view = f"create materialized view v as select AI.GENERATE(body, {CAPPED}).result from t where id = 1"
    assert [item.rule_id for item in lint_sql(view).findings] == ["ASG005"]
