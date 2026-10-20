"""Offline estimates use the price table and the configured assumptions."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from aisql_guard.config import GuardConfig, ModelAssumption
from aisql_guard.estimate import estimate_calls, estimate_paths
from aisql_guard.models import AiCall

ROOT = Path(__file__).resolve().parents[1]


def _call(**overrides: object) -> AiCall:
    values: dict[str, object] = {
        "name": "AI.GENERATE",
        "provider": "bigquery",
        "path": "batch.sql",
        "line": 1,
        "column": 8,
        "model_name": "batch",
        "model_hint": None,
        "has_output_cap": False,
        "output_token_cap": None,
        "has_thinking_budget": False,
        "thinking_token_cap": None,
        "needs_output_cap": True,
        "needs_thinking_budget": True,
    }
    values.update(overrides)
    return AiCall(**values)  # type: ignore[arg-type]


def _config(**overrides: object) -> GuardConfig:
    values: dict[str, object] = {
        "default_model": "google-3.5-flash-lite",
        "default_rows": 1000,
        "default_input_tokens": 1000,
        "default_output_tokens": 100,
        "default_thinking_tokens": 0,
    }
    values.update(overrides)
    return GuardConfig(**values)  # type: ignore[arg-type]


def test_flash_lite_input_and_output() -> None:
    result = estimate_calls([_call()], _config())
    call = result.calls[0]
    assert call.price_id == "google-3.5-flash-lite"
    assert call.input_usd == Decimal("0.30")
    assert call.output_usd == Decimal("0.25")
    assert call.thinking_usd == Decimal("0")
    assert call.total_usd == Decimal("0.55")
    assert result.total_usd == Decimal("0.55")
    assert not result.blocked
    assert any("not included" in note for note in call.notes)
    assert any("assumption" in note for note in call.notes)


def test_thinking_tokens_use_the_output_rate() -> None:
    result = estimate_calls([_call()], _config(default_thinking_tokens=400))
    call = result.calls[0]
    assert call.thinking_usd == Decimal("1.00")
    assert call.total_usd == Decimal("1.55")


def test_sql_caps_override_the_assumptions() -> None:
    call = _call(
        has_output_cap=True,
        output_token_cap=40,
        has_thinking_budget=True,
        thinking_token_cap=0,
    )
    result = estimate_calls([call], _config(default_thinking_tokens=400))
    priced = result.calls[0]
    assert priced.output_tokens == 40
    assert priced.output_usd == Decimal("0.10")
    assert priced.thinking_tokens == 0
    assert priced.thinking_usd == Decimal("0")
    assert any("thinking_budget of 0" in note for note in priced.notes)


def test_thinking_level_keeps_the_assumption() -> None:
    call = _call(has_thinking_budget=True, thinking_token_cap=None)
    result = estimate_calls([call], _config(default_thinking_tokens=50))
    priced = result.calls[0]
    assert priced.thinking_tokens == 50
    assert any("thinking_level" in note for note in priced.notes)


def test_remote_model_path_uses_the_configured_id() -> None:
    call = _call(model_hint="warehouse.remote_models.ticket_model", provider="bigquery")
    config = _config(
        models={"batch": ModelAssumption(model="llama3.1-8b")},
        default_output_tokens=1000,
    )
    result = estimate_calls([call], config)
    priced = result.calls[0]
    assert priced.price_id == "llama3.1-8b"
    assert priced.input_usd == Decimal("0.264")
    assert priced.output_usd == Decimal("0.264")
    assert priced.total_usd == Decimal("0.528")


def test_unknown_model_is_not_treated_as_free() -> None:
    call = _call(model_hint="warehouse.remote_models.ticket_model")
    config = _config(
        default_model="not-a-priced-model",
        total_budget_usd=Decimal("1"),
        model_budgets={"batch": Decimal("1")},
    )
    result = estimate_calls([call], config)
    assert result.calls[0].priced is False
    assert result.calls[0].total_usd is None
    assert result.total_usd is None
    assert result.uncertain
    assert not result.blocked
    assert any("left unpriced" in reason for reason in result.reasons)
    assert any("only checked against known dollars" in reason for reason in result.reasons)


def test_model_and_total_budgets_block() -> None:
    call = _call()
    under = estimate_calls([call], _config(total_budget_usd=Decimal("0.55")))
    assert not under.blocked

    per_model = estimate_calls(
        [call],
        _config(model_budgets={"batch": Decimal("0.10")}),
    )
    assert per_model.blocked
    assert per_model.models[0].over_budget
    assert "0.55 USD" in per_model.reasons[0]

    total = estimate_calls([call, _call(line=2)], _config(total_budget_usd=Decimal("1")))
    assert total.total_usd == Decimal("1.10")
    assert total.blocked
    assert any("project total" in reason for reason in total.reasons)


def test_databricks_dbu_rate_is_not_rounded() -> None:
    call = _call(
        name="AI_QUERY",
        provider="databricks",
        model_hint="llama-3-3-70b",
        needs_thinking_budget=False,
    )
    result = estimate_calls([call], _config(default_output_tokens=100))
    priced = result.calls[0]
    assert priced.input_usd == Decimal("0.50001")
    assert priced.output_usd == Decimal("0.150003")
    assert priced.total_usd == Decimal("0.650013")


def test_example_project_blocks_the_risky_summary() -> None:
    root = ROOT / "examples" / "support_tickets"
    result = estimate_paths([root])
    risky = next(model for model in result.models if model.model_name == "ticket_summaries_risky")
    assert risky.calls[0].price_id == "google-3.5-flash"
    assert risky.calls[0].input_usd == Decimal("112.5")
    assert risky.calls[0].output_usd == Decimal("230.4")
    assert risky.calls[0].thinking_usd == Decimal("921.6")
    assert risky.total_usd == Decimal("1264.5")
    assert risky.over_budget
    capped = next(model for model in result.models if model.model_name == "capped_batch")
    assert capped.calls[0].output_tokens == 128
    assert capped.calls[0].thinking_tokens == 0
    assert capped.calls[0].total_usd == Decimal("0.82")
    assert result.blocked
    assert result.total_usd is not None
    assert result.total_usd > Decimal("50")
    assert result.calls[0].to_dict()["priced"] is True
    payload = result.to_dict()
    assert payload["blocked"] is True
    assert payload["models"]
