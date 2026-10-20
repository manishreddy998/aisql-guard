"""dbt Jinja is rewritten without moving line breaks."""

from __future__ import annotations

from aisql_guard.jinja_sql import extract_materialized, render_for_parse


def test_render_keeps_the_incremental_branch_and_the_line_count() -> None:
    sql = "select 1\n{% if is_incremental() %}\n  and ready\n{% else %}\n  and full\n{% endif %}\n"
    rendered = render_for_parse(sql, incremental=True)
    assert len(rendered.splitlines()) == len(sql.splitlines())
    assert "ready" in rendered
    assert "full" not in rendered
    full = render_for_parse(sql, incremental=False)
    assert "full" in full
    assert "ready" not in full


def test_ref_source_and_this_become_identifiers() -> None:
    sql = (
        "select * from {{ ref('stg_tickets') }} "
        "join {{ source('raw', 'tickets') }} on {{ this }}.id = 1"
    )
    rendered = render_for_parse(sql, incremental=True)
    assert "stg_tickets" in rendered
    assert "raw_tickets" in rendered
    assert "dbt_this" in rendered
    assert "{{" not in rendered


def test_config_materialized_value() -> None:
    sql = "{{ config(materialized='incremental', unique_key='id') }}"
    assert extract_materialized(sql) == "incremental"
    assert extract_materialized("select 1") is None
