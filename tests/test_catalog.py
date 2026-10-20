"""The function catalog is the list of calls the linter knows."""

from __future__ import annotations

from aisql_guard.catalog import SPECS, get_spec


def test_every_spec_is_reachable_by_its_name() -> None:
    assert "AI.GENERATE" in SPECS
    assert "ML.GENERATE_TEXT" in SPECS
    assert "AI_QUERY" in SPECS
    assert "AI_COMPLETE" in SPECS
    generate = get_spec("AI.GENERATE")
    assert generate is not None
    assert generate.provider == "bigquery"
    assert generate.needs_output_cap
    assert generate.needs_thinking_budget


def test_aliases_and_unknown_names() -> None:
    text = get_spec("GENERATE_TEXT")
    assert text is not None
    assert text.display == "ML.GENERATE_TEXT"
    assert get_spec("not_a_function") is None
