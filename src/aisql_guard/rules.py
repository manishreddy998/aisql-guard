"""Rule checks for AI function calls in SQL."""

from __future__ import annotations

from dataclasses import dataclass

from aisql_guard.analyze import (
    CallFact,
    FileFacts,
    has_output_cap,
    has_thinking_budget,
    model_hint,
    output_token_cap,
    position,
    thinking_token_cap,
)
from aisql_guard.models import AiCall, Finding

SEVERITY_RANK = {"note": 0, "warning": 1, "error": 2}


@dataclass(frozen=True)
class Rule:
    """Stable metadata for one lint rule."""

    id: str
    name: str
    severity: str
    summary: str
    fix: str


RULES: dict[str, Rule] = {
    "ASG000": Rule(
        id="ASG000",
        name="parse-failed",
        severity="warning",
        summary="SQL could not be parsed, so structural checks were skipped.",
        fix=(
            "Fix the SQL syntax or set the warehouse dialect. Jinja other than "
            "ref, source, this, and config is replaced with NULL before parsing."
        ),
    ),
    "ASG001": Rule(
        id="ASG001",
        name="missing-output-cap",
        severity="error",
        summary="AI call has no output token cap.",
        fix=(
            "Set max_output_tokens on a BigQuery call (STRUCT field or "
            "generation_config inside model_params). Set max_tokens on "
            "Databricks ai_query and Snowflake AI_COMPLETE."
        ),
    ),
    "ASG002": Rule(
        id="ASG002",
        name="missing-thinking-budget",
        severity="error",
        summary="BigQuery AI call has no thinking budget.",
        fix=(
            "Set thinking_config.thinking_budget in model_params. Use 0 when "
            "the task does not need reasoning. On newer models, set "
            "thinking_config.thinking_level instead."
        ),
    ),
    "ASG003": Rule(
        id="ASG003",
        name="limit-does-not-cut-work",
        severity="error",
        summary="LIMIT or OFFSET does not reduce the rows sent to the model.",
        fix=(
            "Filter with WHERE, write those rows to a table, and read that "
            "table from the AI function. LIMIT inside the prompt query still "
            "processes the input first, and LIMIT on the outer SELECT runs "
            "after the function."
        ),
    ),
    "ASG004": Rule(
        id="ASG004",
        name="incremental-reruns-generated-rows",
        severity="error",
        summary="Incremental model does not skip rows that were already generated.",
        fix=(
            "Exclude keys already stored in {{ this }} before the AI call. "
            "NOT EXISTS, a left join that keeps only null matches, or a "
            "watermark against {{ this }} all work. unique_key alone still "
            "sends every source row to the model."
        ),
    ),
    "ASG005": Rule(
        id="ASG005",
        name="ai-call-in-view",
        severity="error",
        summary="AI call is in a view or an ephemeral model.",
        fix=(
            "Materialize the AI call as a table or an incremental model, and "
            "let later models read the stored result. A view runs the call "
            "again on every read."
        ),
    ),
    "ASG006": Rule(
        id="ASG006",
        name="unfiltered-table",
        severity="warning",
        summary="AI call reads an unfiltered table.",
        fix=(
            "Add a WHERE clause, or build a smaller upstream model, so only "
            "the rows that need a result are sent."
        ),
    ),
    "ASG007": Rule(
        id="ASG007",
        name="select-star-prompt",
        severity="warning",
        summary="SELECT * is passed into the prompt.",
        fix=(
            "Pass only the columns the prompt needs, for example a CONCAT of "
            "the text fields. Every extra column is billed as input."
        ),
    ),
}


def build_findings(
    facts: FileFacts,
    sql: str,
    *,
    path: str,
    model_name: str | None,
    materialization: str | None,
    check_incremental: bool,
    disabled: set[str],
) -> tuple[list[Finding], list[AiCall]]:
    """Turn parse facts into findings and call records."""

    findings: list[Finding] = []
    calls: list[AiCall] = []
    if facts.parse_failed:
        _add(
            findings,
            disabled,
            _finding(sql, "ASG000", path, model_name, 0, RULES["ASG000"].summary),
        )
    for fact in facts.calls:
        calls.append(_call_record(fact, sql, path, model_name))
        findings.extend(
            _call_findings(
                fact,
                sql,
                path=path,
                model_name=model_name,
                disabled=disabled,
                skip_unfiltered=facts.has_skip_filter,
            )
        )
    if facts.calls:
        _add_model_findings(
            findings,
            facts,
            sql,
            path=path,
            model_name=model_name,
            materialization=materialization,
            check_incremental=check_incremental,
            disabled=disabled,
        )
    return findings, calls


def _call_findings(
    fact: CallFact,
    sql: str,
    *,
    path: str,
    model_name: str | None,
    disabled: set[str],
    skip_unfiltered: bool,
) -> list[Finding]:
    spec = fact.site.spec
    display = spec.display
    findings: list[Finding] = []
    if spec.needs_output_cap and not has_output_cap(fact.site.text):
        _add(
            findings,
            disabled,
            _finding(
                sql,
                "ASG001",
                path,
                model_name,
                fact.site.start,
                (
                    f"{display} does not set an output token cap. The response "
                    "can run long, and you pay for every output token."
                ),
            ),
        )
    if spec.needs_thinking_budget and not has_thinking_budget(fact.site.text):
        _add(
            findings,
            disabled,
            _finding(
                sql,
                "ASG002",
                path,
                model_name,
                fact.site.start,
                (
                    f"{display} does not set a thinking budget or thinking level. "
                    "Thinking tokens are billed at the output token rate."
                ),
            ),
        )
    if fact.prompt_limit_at is not None:
        _add(
            findings,
            disabled,
            _finding(
                sql,
                "ASG003",
                path,
                model_name,
                fact.prompt_limit_at,
                (
                    f"{display} reads a prompt query that uses LIMIT or OFFSET. "
                    "BigQuery processes that input before it applies the clause, "
                    "so the clause does not cut the model work."
                ),
            ),
        )
    if fact.outer_limit_at is not None:
        _add(
            findings,
            disabled,
            _finding(
                sql,
                "ASG003",
                path,
                model_name,
                fact.outer_limit_at,
                (
                    f"LIMIT or OFFSET sits outside {display}. The function runs "
                    "for the rows that reach the SELECT list, and the clause "
                    "applies only after that."
                ),
            ),
        )
    if fact.upstream_limit_at is not None:
        _add(
            findings,
            disabled,
            _finding(
                sql,
                "ASG003",
                path,
                model_name,
                fact.upstream_limit_at,
                (
                    f"A LIMIT or OFFSET in the rows that feed {display} is not "
                    "a reliable cap. Write the sample to a table first, then "
                    "point the function at that table."
                ),
            ),
        )
    if fact.unfiltered and not skip_unfiltered:
        _add(
            findings,
            disabled,
            _finding(
                sql,
                "ASG006",
                path,
                model_name,
                fact.site.start,
                (
                    f"{display} reads an unfiltered table. Every row that "
                    "reaches it becomes a model call."
                ),
            ),
        )
    if fact.has_star:
        _add(
            findings,
            disabled,
            _finding(
                sql,
                "ASG007",
                path,
                model_name,
                fact.site.start,
                (
                    f"{display} is fed SELECT *. Every column is packed into "
                    "the prompt and billed as input."
                ),
            ),
        )
    return findings


def _add_model_findings(
    findings: list[Finding],
    facts: FileFacts,
    sql: str,
    *,
    path: str,
    model_name: str | None,
    materialization: str | None,
    check_incremental: bool,
    disabled: set[str],
) -> None:
    first = facts.calls[0].site.start
    display = facts.calls[0].site.spec.display
    kind = (materialization or "").lower()
    if check_incremental and kind == "incremental" and not facts.has_skip_filter:
        _add(
            findings,
            disabled,
            _finding(
                sql,
                "ASG004",
                path,
                model_name,
                first,
                (
                    f"This incremental model calls {display} but does not skip "
                    "rows that already have a generated value. The next run "
                    "sends those rows to the model again."
                ),
            ),
        )
    if kind in {"view", "ephemeral"} or facts.is_view_sql:
        where = "an ephemeral model" if kind == "ephemeral" else "a view"
        _add(
            findings,
            disabled,
            _finding(
                sql,
                "ASG005",
                path,
                model_name,
                first,
                (
                    f"{display} is in {where}. The call runs again on every "
                    "read, so the bill repeats."
                ),
            ),
        )


def _call_record(fact: CallFact, sql: str, path: str, model_name: str | None) -> AiCall:
    spec = fact.site.spec
    line, column = position(sql, fact.site.start)
    return AiCall(
        name=spec.display,
        provider=spec.provider,
        path=path,
        line=line,
        column=column,
        model_name=model_name,
        model_hint=model_hint(spec.name, fact.site.text),
        has_output_cap=has_output_cap(fact.site.text),
        output_token_cap=output_token_cap(fact.site.text),
        has_thinking_budget=has_thinking_budget(fact.site.text),
        thinking_token_cap=thinking_token_cap(fact.site.text),
        needs_output_cap=spec.needs_output_cap,
        needs_thinking_budget=spec.needs_thinking_budget,
    )


def _finding(
    sql: str,
    rule_id: str,
    path: str,
    model_name: str | None,
    offset: int,
    message: str,
) -> Finding:
    rule = RULES[rule_id]
    line, column = position(sql, offset)
    return Finding(
        rule_id=rule.id,
        severity=rule.severity,
        message=message,
        fix=rule.fix,
        path=path,
        line=line,
        column=column,
        model_name=model_name,
    )


def _add(findings: list[Finding], disabled: set[str], finding: Finding) -> None:
    if finding.rule_id not in disabled:
        findings.append(finding)
