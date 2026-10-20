"""Rule checks against inline SQL."""

from __future__ import annotations

from aisql_guard.engine import lint_sql
from aisql_guard.jinja_sql import extract_materialized

CAPPED = (
    "model_params => JSON "
    '\'{"generation_config":{"max_output_tokens":128,"thinking_config":{"thinking_budget":0}}}\''
)


def _ids(sql: str, **kwargs: object) -> list[str]:
    result = lint_sql(sql, **kwargs)  # type: ignore[arg-type]
    return [finding.rule_id for finding in result.findings]


def test_uncapped_generate_needs_a_cap_and_a_thinking_budget() -> None:
    sql = """
    select AI.GENERATE(concat('Summarize: ', body)).result as summary
    from tickets
    where status = 'closed'
    """
    assert _ids(sql, materialization="table") == ["ASG001", "ASG002"]


def test_output_cap_without_thinking_budget() -> None:
    sql = """
    select AI.GENERATE(
      concat('Summarize: ', body),
      model_params => JSON '{"generation_config":{"max_output_tokens":128}}'
    ).result
    from tickets
    where status = 'closed'
    """
    assert _ids(sql, materialization="table") == ["ASG002"]


def test_struct_cap_and_thinking_budget_are_enough() -> None:
    sql = """
    select *
    from ML.GENERATE_TEXT(
      model `warehouse.remote_models.ticket_model`,
      (
        select concat('Summarize: ', body) as prompt
        from tickets
        where status = 'open'
      ),
      struct(
        128 as max_output_tokens,
        '{"generation_config":{"thinking_config":{"thinking_budget":0}}}' as model_params
      )
    )
    """
    assert _ids(sql, materialization="table") == []


def test_limit_inside_the_prompt_query() -> None:
    sql = """
    select *
    from ML.GENERATE_TEXT(
      model `warehouse.remote_models.ticket_model`,
      (
        select concat('Summarize: ', body) as prompt
        from tickets
        where status = 'open'
        limit 100
      ),
      struct(
        128 as max_output_tokens,
        '{"generation_config":{"thinking_config":{"thinking_budget":0}}}' as model_params
      )
    )
    """
    findings = lint_sql(sql, materialization="table").findings
    assert [item.rule_id for item in findings] == ["ASG003"]
    assert "prompt query" in findings[0].message
    assert findings[0].line == sql.splitlines().index("        limit 100") + 1


def test_outer_limit_runs_after_the_function() -> None:
    sql = f"""
    select AI.GENERATE(
      concat('Summarize: ', body),
      {CAPPED}
    ).result as summary
    from tickets
    where status = 'closed'
    limit 50
    """
    findings = lint_sql(sql, materialization="table").findings
    assert [item.rule_id for item in findings] == ["ASG003"]
    assert "outside" in findings[0].message


def test_limit_in_a_cte_is_not_a_reliable_cap() -> None:
    sql = f"""
    with sample as (
      select body from tickets limit 10
    )
    select AI.GENERATE(
      concat('Summarize: ', body),
      {CAPPED}
    ).result as summary
    from sample
    """
    ids = _ids(sql, materialization="table")
    # LIMIT does not count as a filter, so the tickets table is still open.
    assert ids == ["ASG003", "ASG006"]


def test_limit_inside_a_string_is_not_a_clause() -> None:
    sql = f"""
    select AI.GENERATE(
      concat('Do not add a LIMIT clause. Summarize: ', body),
      {CAPPED}
    ).result as summary
    from tickets
    where status = 'closed'
    """
    assert _ids(sql, materialization="table") == []


def test_incremental_without_a_skip_filter() -> None:
    sql = f"""
    select AI.GENERATE(concat('Summarize: ', body), {CAPPED}).result as summary
    from tickets
    where status = 'closed'
    """
    assert _ids(sql, materialization="incremental") == ["ASG004"]


def test_not_exists_against_this_skips_generated_rows() -> None:
    sql = """
    {{ config(materialized='incremental', unique_key='ticket_id') }}
    select src.ticket_id,
      AI.GENERATE(
        concat('Summarize: ', src.body),
        model_params => JSON '{"generation_config":{"max_output_tokens":64,"thinking_config":{"thinking_level":"LOW"}}}'
      ).result as summary
    from {{ ref('stg_tickets') }} as src
    where src.status = 'closed'
    {% if is_incremental() %}
      and not exists (
        select 1 from {{ this }} as already
        where already.ticket_id = src.ticket_id
          and already.summary is not null
      )
    {% endif %}
    """
    assert _ids(sql) == []


def test_not_in_and_watermark_and_left_join_count_as_skip_filters() -> None:
    base = (
        "select AI.GENERATE(body, "
        'model_params => JSON \'{"generation_config":{"max_output_tokens":8,'
        '"thinking_config":{"thinking_budget":0}}}\').result from src'
    )
    not_in = base + " where ticket_id not in (select ticket_id from {{ this }})"
    watermark = base + " where updated_at > (select max(updated_at) from {{ this }})"
    anti = """
    select AI.GENERATE(
      body,
      model_params => JSON '{"generation_config":{"max_output_tokens":8,"thinking_config":{"thinking_budget":0}}}'
    ).result
    from src
    left join {{ this }} as already on already.ticket_id = src.ticket_id
    where already.ticket_id is null
    """
    assert _ids(not_in, materialization="incremental") == []
    assert _ids(watermark, materialization="incremental") == []
    assert _ids(anti, materialization="incremental") == []


def test_keeping_existing_keys_does_not_count_as_a_skip() -> None:
    sql = """
    select AI.GENERATE(
      body,
      model_params => JSON '{"generation_config":{"max_output_tokens":8,"thinking_config":{"thinking_budget":0}}}'
    ).result
    from src
    where ticket_id in (select ticket_id from {{ this }})
    """
    assert _ids(sql, materialization="incremental") == ["ASG004"]


def test_view_and_ephemeral_models_rerun_on_read() -> None:
    sql = """
    select AI.IF(
      ('billing? ', body),
      model_params => JSON '{"generation_config":{"thinking_config":{"thinking_budget":0}}}'
    )
    from tickets
    where priority = 'high'
    """
    assert _ids(sql, materialization="ephemeral") == ["ASG005"]
    view = f"create view v as select AI.GENERATE(body, {CAPPED}).result from tickets where id = 1"
    assert _ids(view) == ["ASG005"]


def test_unfiltered_table_and_filtered_cte() -> None:
    sql = f"select AI.SCORE(('rate ', body), {CAPPED}).urgency from tickets"
    assert _ids(sql, materialization="table") == ["ASG006"]
    filtered = f"""
    with kept as (select body from tickets where status = 'open')
    select AI.SCORE(('rate ', body), {CAPPED}).urgency from kept
    """
    assert _ids(filtered, materialization="table") == []


def test_table_prompt_argument_is_unfiltered() -> None:
    sql = """
    select *
    from ML.GENERATE_TEXT(
      model `warehouse.remote_models.ticket_model`,
      table `warehouse.raw.prompts`,
      struct(
        32 as max_output_tokens,
        '{"generation_config":{"thinking_config":{"thinking_budget":0}}}' as model_params
      )
    )
    """
    assert _ids(sql, materialization="table") == ["ASG006"]


def test_select_star_in_the_prompt() -> None:
    sql = f"""
    select AI.GENERATE(
      (select as struct src.*),
      {CAPPED}
    ).result
    from tickets as src
    where priority = 'high'
    """
    findings = lint_sql(sql, materialization="table").findings
    assert [item.rule_id for item in findings] == ["ASG007"]


def test_outer_star_from_a_table_function_is_not_the_prompt() -> None:
    sql = """
    select *
    from ML.GENERATE_TEXT(
      model `warehouse.remote_models.ticket_model`,
      (select body as prompt from tickets where status = 'open'),
      struct(
        32 as max_output_tokens,
        '{"generation_config":{"thinking_config":{"thinking_budget":0}}}' as model_params
      )
    )
    """
    assert _ids(sql, materialization="table") == []


def test_disabled_rule_is_silent() -> None:
    sql = "select AI.GENERATE(body).result from tickets where id = 1"
    assert "ASG001" not in _ids(sql, materialization="table", disabled={"ASG001"})


def test_databricks_requires_max_tokens_but_not_a_thinking_budget() -> None:
    sql = """
    select ai_query('llama-3-3-70b', concat('Summarize: ', body)) as summary
    from tickets
    where status = 'open'
    """
    assert _ids(sql, dialect="databricks", materialization="table") == ["ASG001"]
    capped = """
    select ai_query(
      'llama-3-3-70b',
      concat('Summarize: ', body),
      modelParameters => named_struct('max_tokens', 64)
    ) as summary
    from tickets
    where status = 'open'
    """
    result = lint_sql(capped, dialect="databricks", materialization="table")
    assert result.findings == []
    assert result.calls[0].model_hint == "llama-3-3-70b"
    assert result.calls[0].output_token_cap == 64


def test_snowflake_complete_with_max_tokens() -> None:
    sql = """
    select AI_COMPLETE('llama3.1-8b', body, {'max_tokens': 32}) as summary
    from tickets
    where status = 'open'
    """
    result = lint_sql(sql, dialect="snowflake", materialization="table")
    assert result.findings == []
    assert result.calls[0].provider == "snowflake"
    assert result.calls[0].output_token_cap == 32


def test_parse_failure_is_a_warning_and_still_sees_the_call() -> None:
    sql = "select AI.GENERATE(body from tickets"
    ids = _ids(sql, materialization="table")
    assert ids[0] == "ASG000"
    assert "ASG001" in ids
    assert "ASG002" in ids


def test_config_materialization_overrides_the_argument() -> None:
    sql = """
    {{ config(materialized='view') }}
    select AI.GENERATE(
      body,
      model_params => JSON '{"generation_config":{"max_output_tokens":8,"thinking_config":{"thinking_budget":0}}}'
    ).result
    from tickets
    where id = 1
    """
    assert extract_materialized(sql) == "view"
    assert _ids(sql, materialization="table") == ["ASG005"]
