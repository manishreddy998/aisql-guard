"""Lint SQL files and dbt projects for AI function cost surprises."""

from __future__ import annotations

from pathlib import Path

from aisql_guard.analyze import analyze
from aisql_guard.discover import (
    find_dbt_project,
    in_model_path,
    iter_sql_files,
    project_materialization,
    relative_path,
)
from aisql_guard.jinja_sql import extract_materialized, render_for_parse
from aisql_guard.models import AiCall, Finding, LintResult
from aisql_guard.rules import build_findings


def lint_sql(
    sql: str,
    *,
    path: str = "query.sql",
    dialect: str = "bigquery",
    materialization: str | None = None,
    model_name: str | None = None,
    disabled: set[str] | None = None,
) -> LintResult:
    """Lint one SQL string.

    dbt ``config()`` in the text overrides ``materialization`` when it sets
    ``materialized``.
    """

    sql_materialization = extract_materialized(sql)
    effective = sql_materialization or materialization
    skipped = set(disabled or ())
    incremental_sql = render_for_parse(sql, incremental=True)
    full_sql = render_for_parse(sql, incremental=False)
    findings, calls = _lint_rendered(
        incremental_sql,
        path=path,
        dialect=dialect,
        materialization=effective,
        model_name=model_name,
        check_incremental=True,
        disabled=skipped,
    )
    if full_sql != incremental_sql:
        more_findings, more_calls = _lint_rendered(
            full_sql,
            path=path,
            dialect=dialect,
            materialization=effective,
            model_name=model_name,
            check_incremental=False,
            disabled=skipped,
        )
        findings.extend(more_findings)
        calls.extend(more_calls)
    return LintResult(findings=_dedupe_findings(findings), calls=_dedupe_calls(calls))


def lint_paths(
    paths: list[Path],
    *,
    dialect: str = "bigquery",
    disabled: set[str] | None = None,
) -> LintResult:
    """Lint SQL files and, when a dbt project is present, its models."""

    findings: list[Finding] = []
    calls: list[AiCall] = []
    for path in paths:
        root = path if path.is_dir() else path.parent
        for sql_file in iter_sql_files(path):
            project = find_dbt_project(sql_file)
            materialization: str | None = None
            if project is not None and in_model_path(project, sql_file):
                materialization = project_materialization(project, sql_file)
                schema_value = project.schema_materializations.get(sql_file.stem)
                if schema_value:
                    materialization = schema_value
                if materialization is None:
                    materialization = "view"
            text = sql_file.read_text(encoding="utf-8")
            result = lint_sql(
                text,
                path=relative_path(sql_file, root),
                dialect=dialect,
                materialization=materialization,
                model_name=sql_file.stem,
                disabled=disabled,
            )
            findings.extend(result.findings)
            calls.extend(result.calls)
    return LintResult(findings=_dedupe_findings(findings), calls=calls)


def _lint_rendered(
    sql: str,
    *,
    path: str,
    dialect: str,
    materialization: str | None,
    model_name: str | None,
    check_incremental: bool,
    disabled: set[str],
) -> tuple[list[Finding], list[AiCall]]:
    facts = analyze(sql, dialect=dialect)
    return build_findings(
        facts,
        sql,
        path=path,
        model_name=model_name,
        materialization=materialization,
        check_incremental=check_incremental,
        disabled=disabled,
    )


def _dedupe_findings(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str, int, int, str]] = set()
    unique: list[Finding] = []
    ordered = sorted(findings, key=lambda item: (item.path, item.line, item.column, item.rule_id))
    for finding in ordered:
        key = (finding.path, finding.rule_id, finding.line, finding.column, finding.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique


def _dedupe_calls(calls: list[AiCall]) -> list[AiCall]:
    seen: set[tuple[str, str, int, int]] = set()
    unique: list[AiCall] = []
    for call in calls:
        key = (call.path, call.name, call.line, call.column)
        if key in seen:
            continue
        seen.add(key)
        unique.append(call)
    return unique
