"""AI SQL functions that call a model once per row."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FunctionSpec:
    """How one function should be linted."""

    name: str
    display: str
    provider: str
    needs_output_cap: bool
    needs_thinking_budget: bool


def _spec(
    name: str,
    display: str,
    provider: str,
    *,
    output_cap: bool,
    thinking: bool,
) -> FunctionSpec:
    return FunctionSpec(
        name=name,
        display=display,
        provider=provider,
        needs_output_cap=output_cap,
        needs_thinking_budget=thinking,
    )


_SPECS: tuple[FunctionSpec, ...] = (
    _spec("AI.GENERATE", "AI.GENERATE", "bigquery", output_cap=True, thinking=True),
    _spec("AI.GENERATE_TEXT", "AI.GENERATE_TEXT", "bigquery", output_cap=True, thinking=True),
    _spec("AI.GENERATE_TABLE", "AI.GENERATE_TABLE", "bigquery", output_cap=True, thinking=True),
    _spec("AI.GENERATE_BOOL", "AI.GENERATE_BOOL", "bigquery", output_cap=False, thinking=True),
    _spec("AI.GENERATE_INT", "AI.GENERATE_INT", "bigquery", output_cap=False, thinking=True),
    _spec("AI.GENERATE_DOUBLE", "AI.GENERATE_DOUBLE", "bigquery", output_cap=False, thinking=True),
    _spec("AI.IF", "AI.IF", "bigquery", output_cap=False, thinking=True),
    _spec("AI.CLASSIFY", "AI.CLASSIFY", "bigquery", output_cap=False, thinking=True),
    _spec("AI.SCORE", "AI.SCORE", "bigquery", output_cap=False, thinking=True),
    _spec("AI.AGG", "AI.AGG", "bigquery", output_cap=True, thinking=True),
    _spec("ML.GENERATE_TEXT", "ML.GENERATE_TEXT", "bigquery", output_cap=True, thinking=True),
    _spec("AI_QUERY", "ai_query", "databricks", output_cap=True, thinking=False),
    _spec("AI_CLASSIFY", "ai_classify", "databricks", output_cap=False, thinking=False),
    _spec("AI_EXTRACT", "ai_extract", "databricks", output_cap=True, thinking=False),
    _spec("AI_SUMMARIZE", "ai_summarize", "databricks", output_cap=True, thinking=False),
    _spec("AI_TRANSLATE", "ai_translate", "databricks", output_cap=True, thinking=False),
    _spec("AI_PARSE_DOCUMENT", "ai_parse_document", "databricks", output_cap=True, thinking=False),
    _spec("AI_FIX_GRAMMAR", "ai_fix_grammar", "databricks", output_cap=True, thinking=False),
    _spec(
        "AI_ANALYZE_SENTIMENT",
        "ai_analyze_sentiment",
        "databricks",
        output_cap=False,
        thinking=False,
    ),
    _spec("AI_MASK", "ai_mask", "databricks", output_cap=False, thinking=False),
    _spec("AI_COMPLETE", "AI_COMPLETE", "snowflake", output_cap=True, thinking=False),
    _spec("AI_FILTER", "AI_FILTER", "snowflake", output_cap=False, thinking=False),
    _spec("AI_SENTIMENT", "AI_SENTIMENT", "snowflake", output_cap=False, thinking=False),
    _spec(
        "SNOWFLAKE.CORTEX.COMPLETE",
        "SNOWFLAKE.CORTEX.COMPLETE",
        "snowflake",
        output_cap=True,
        thinking=False,
    ),
    _spec(
        "SNOWFLAKE.CORTEX.SUMMARIZE",
        "SNOWFLAKE.CORTEX.SUMMARIZE",
        "snowflake",
        output_cap=True,
        thinking=False,
    ),
    _spec(
        "SNOWFLAKE.CORTEX.TRANSLATE",
        "SNOWFLAKE.CORTEX.TRANSLATE",
        "snowflake",
        output_cap=True,
        thinking=False,
    ),
    _spec(
        "SNOWFLAKE.CORTEX.EXTRACT_ANSWER",
        "SNOWFLAKE.CORTEX.EXTRACT_ANSWER",
        "snowflake",
        output_cap=True,
        thinking=False,
    ),
    _spec(
        "SNOWFLAKE.CORTEX.SENTIMENT",
        "SNOWFLAKE.CORTEX.SENTIMENT",
        "snowflake",
        output_cap=False,
        thinking=False,
    ),
    _spec(
        "SNOWFLAKE.CORTEX.CLASSIFY_TEXT",
        "SNOWFLAKE.CORTEX.CLASSIFY_TEXT",
        "snowflake",
        output_cap=False,
        thinking=False,
    ),
)

SPECS: dict[str, FunctionSpec] = {spec.name: spec for spec in _SPECS}

# Names sqlglot may surface without a schema prefix.
ALIASES: dict[str, str] = {
    "GENERATE_TEXT": "ML.GENERATE_TEXT",
    "GENERATE_TABLE": "AI.GENERATE_TABLE",
    "GENERATE_BOOL": "AI.GENERATE_BOOL",
    "GENERATE_INT": "AI.GENERATE_INT",
    "GENERATE_DOUBLE": "AI.GENERATE_DOUBLE",
    "AI_GENERATE": "AI.GENERATE",
}

# Longer names first so GENERATE_TEXT wins over GENERATE.
SCAN_NAMES: tuple[str, ...] = tuple(sorted(SPECS, key=len, reverse=True))


def get_spec(name: str) -> FunctionSpec | None:
    """Return the catalog entry for a function name."""

    key = name.upper()
    if key in SPECS:
        return SPECS[key]
    alias = ALIASES.get(key)
    if alias is None:
        return None
    return SPECS.get(alias)
