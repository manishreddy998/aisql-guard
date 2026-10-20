"""Estimate AI function cost from assumptions and the published price table.

The formula is rows times tokens, divided by one million, times the USD
rate. Thinking tokens use the output rate. A numeric cap in the SQL
replaces the matching assumption. Calls with no published price stay
unpriced and are not counted as zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from aisql_guard.config import GuardConfig, LiveSample, load_config
from aisql_guard.engine import lint_paths
from aisql_guard.live import LiveProbe, build_count_tokens_sql
from aisql_guard.models import AiCall
from aisql_guard.pricing import ModelPrice, PriceCatalog, load_pricing

_MILLION = Decimal(1_000_000)
_CENT = Decimal("0.000001")


@dataclass
class CallEstimate:
    """Cost of one AI function call."""

    path: str
    line: int
    model_name: str | None
    function: str
    provider: str
    price_id: str | None
    rows: int
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    input_usd: Decimal | None
    output_usd: Decimal | None
    thinking_usd: Decimal | None
    total_usd: Decimal | None
    priced: bool
    dry_run_bytes: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line": self.line,
            "model_name": self.model_name,
            "function": self.function,
            "provider": self.provider,
            "price_id": self.price_id,
            "rows": self.rows,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "input_usd": _money(self.input_usd),
            "output_usd": _money(self.output_usd),
            "thinking_usd": _money(self.thinking_usd),
            "total_usd": _money(self.total_usd),
            "priced": self.priced,
            "dry_run_bytes": self.dry_run_bytes,
            "notes": list(self.notes),
        }


@dataclass
class ModelEstimate:
    """Every call in one dbt model, or one SQL file."""

    model_name: str
    calls: list[CallEstimate]
    total_usd: Decimal | None
    budget_usd: Decimal | None = None
    priced: bool = True
    over_budget: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "model_name": self.model_name,
            "total_usd": _money(self.total_usd),
            "budget_usd": _money(self.budget_usd),
            "priced": self.priced,
            "over_budget": self.over_budget,
            "calls": [call.to_dict() for call in self.calls],
        }


@dataclass
class EstimateResult:
    """Project estimate and whether a configured budget blocks the run."""

    models: list[ModelEstimate] = field(default_factory=list)
    calls: list[CallEstimate] = field(default_factory=list)
    total_usd: Decimal | None = None
    total_budget_usd: Decimal | None = None
    blocked: bool = False
    uncertain: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "models": [model.to_dict() for model in self.models],
            "total_usd": _money(self.total_usd),
            "total_budget_usd": _money(self.total_budget_usd),
            "blocked": self.blocked,
            "uncertain": self.uncertain,
            "reasons": list(self.reasons),
        }


def estimate_paths(
    paths: list[Path],
    config: GuardConfig | None = None,
    *,
    live: bool = False,
    probe: LiveProbe | None = None,
) -> EstimateResult:
    """Lint the paths and price every AI call they contain.

    ``live=True`` is the only switch that may open a BigQuery client. A
    config file on its own never does.
    """

    if config is None:
        start = paths[0] if paths else Path.cwd()
        config = load_config(start)
    linted = lint_paths(
        list(paths),
        dialect=config.dialect,
        disabled=set(config.disabled_rules),
    )
    return estimate_calls(linted.calls, config, live=live, probe=probe)


def estimate_calls(
    calls: list[AiCall],
    config: GuardConfig,
    catalog: PriceCatalog | None = None,
    *,
    live: bool = False,
    probe: LiveProbe | None = None,
) -> EstimateResult:
    """Price calls that have already been found."""

    prices = catalog or load_pricing()
    readings: dict[str, _LiveReading] = {}
    if live:
        readings = _collect_samples(calls, config, _probe_for(config, probe))
    priced_calls = [
        _price_call(call, config, prices, readings.get(call.model_name or call.path))
        for call in calls
    ]
    return _assemble(priced_calls, config)


@dataclass
class _LiveReading:
    input_tokens: int | None
    dry_run_bytes: int | None
    notes: list[str]


def _probe_for(config: GuardConfig, probe: LiveProbe | None) -> LiveProbe:
    if probe is not None:
        return probe
    from aisql_guard.live import connect_bigquery

    return connect_bigquery(config.live_project, config.live_location)


def _collect_samples(
    calls: list[AiCall],
    config: GuardConfig,
    probe: LiveProbe,
) -> dict[str, _LiveReading]:
    readings: dict[str, _LiveReading] = {}
    for call in calls:
        key = call.model_name or call.path
        if key in readings:
            continue
        readings[key] = _read_sample(config.live_models.get(key), config, probe)
    return readings


def _read_sample(
    sample: LiveSample | None,
    config: GuardConfig,
    probe: LiveProbe,
) -> _LiveReading:
    notes: list[str] = []
    input_tokens: int | None = None
    dry_bytes: int | None = None
    if sample is None or not _sample_has_tokens(sample):
        notes.append(
            "live mode left input tokens as an assumption because no sample SQL is configured"
        )
    else:
        sql = build_count_tokens_sql(sample, config.live_sample_rows)
        average = probe.average_input_tokens(sql)
        input_tokens = int(Decimal(str(average)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        if sample.count_tokens_sql:
            notes.append("input tokens use the average from the configured AI.COUNT_TOKENS query")
        else:
            notes.append(
                "input tokens use the average from AI.COUNT_TOKENS "
                f"on a sample of {config.live_sample_rows} rows"
            )
    if sample is not None and sample.dry_run_sql:
        dry_bytes = probe.dry_run_bytes(sample.dry_run_sql)
        notes.append(
            f"dry run reports {dry_bytes} bytes. Query compute is billed separately "
            "and is not part of this estimate."
        )
    return _LiveReading(input_tokens=input_tokens, dry_run_bytes=dry_bytes, notes=notes)


def _sample_has_tokens(sample: LiveSample) -> bool:
    return bool(sample.count_tokens_sql or (sample.prompt and sample.source))


def _price_call(
    call: AiCall,
    config: GuardConfig,
    catalog: PriceCatalog,
    reading: _LiveReading | None = None,
) -> CallEstimate:
    assumption = config.assumption_for(call.model_name)
    rows = assumption.rows
    input_tokens = assumption.input_tokens
    output_tokens = assumption.output_tokens
    thinking_tokens = assumption.thinking_tokens
    notes: list[str] = []
    dry_bytes: int | None = None
    if reading is not None:
        notes.extend(reading.notes)
        dry_bytes = reading.dry_run_bytes
        if reading.input_tokens is not None:
            input_tokens = reading.input_tokens

    if call.output_token_cap is not None:
        if call.output_token_cap < output_tokens:
            notes.append(
                "output tokens use the SQL cap of "
                f"{call.output_token_cap}, which is under the configured {output_tokens}"
            )
        output_tokens = min(output_tokens, call.output_token_cap)
    elif call.needs_output_cap and not call.has_output_cap:
        notes.append(
            "output tokens are an assumption because the SQL does not set max_output_tokens"
        )

    if call.thinking_token_cap is not None:
        if call.thinking_token_cap != thinking_tokens:
            notes.append(
                f"thinking tokens use the SQL thinking_budget of {call.thinking_token_cap}"
            )
        thinking_tokens = call.thinking_token_cap
    elif call.has_thinking_budget:
        notes.append(
            "thinking_level is set without a numeric budget, "
            "so the configured thinking token assumption is used"
        )
    elif thinking_tokens == 0 and call.needs_thinking_budget:
        notes.append(
            "thinking cost is not included because no thinking budget is set "
            "and the assumption is 0"
        )

    price = _resolve_price(call, assumption.model, config.default_model, catalog)
    if price is None:
        hint = call.model_hint or assumption.model or config.default_model
        notes.append(
            f"no published price matched {hint}. "
            "Unpriced calls are not counted as 0 USD."
        )
        return _unpriced(
            call, rows, input_tokens, output_tokens, thinking_tokens, notes, dry_bytes
        )

    input_rate, output_rate = price.usd_rates(
        snowflake_credit_usd=config.snowflake_ai_credit_usd,
        databricks_usd_per_dbu=config.databricks_usd_per_dbu,
    )
    input_usd = _line_cost(rows, input_tokens, input_rate)
    output_usd = _line_cost(rows, output_tokens, output_rate)
    thinking_usd = _line_cost(rows, thinking_tokens, output_rate)
    return CallEstimate(
        path=call.path,
        line=call.line,
        model_name=call.model_name,
        function=call.name,
        provider=call.provider,
        price_id=price.id,
        rows=rows,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        input_usd=input_usd,
        output_usd=output_usd,
        thinking_usd=thinking_usd,
        total_usd=input_usd + output_usd + thinking_usd,
        priced=True,
        dry_run_bytes=dry_bytes,
        notes=notes,
    )


def _resolve_price(
    call: AiCall,
    configured_model: str | None,
    default_model: str,
    catalog: PriceCatalog,
) -> ModelPrice | None:
    candidates: list[str] = []
    if call.model_hint:
        candidates.append(call.model_hint)
    if configured_model:
        candidates.append(configured_model)
    if default_model:
        candidates.append(default_model)
    for name in candidates:
        match = catalog.resolve(name, provider=call.provider)
        if match is None:
            match = catalog.resolve(name)
        if match is not None:
            return match
    return None


def _unpriced(
    call: AiCall,
    rows: int,
    input_tokens: int,
    output_tokens: int,
    thinking_tokens: int,
    notes: list[str],
    dry_bytes: int | None,
) -> CallEstimate:
    return CallEstimate(
        path=call.path,
        line=call.line,
        model_name=call.model_name,
        function=call.name,
        provider=call.provider,
        price_id=None,
        rows=rows,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        input_usd=None,
        output_usd=None,
        thinking_usd=None,
        total_usd=None,
        priced=False,
        dry_run_bytes=dry_bytes,
        notes=notes,
    )


def _assemble(calls: list[CallEstimate], config: GuardConfig) -> EstimateResult:
    grouped: dict[str, list[CallEstimate]] = {}
    for call in calls:
        grouped.setdefault(call.model_name or call.path, []).append(call)

    models: list[ModelEstimate] = []
    reasons: list[str] = []
    blocked = False
    for name in sorted(grouped):
        group = grouped[name]
        known = [call.total_usd for call in group if call.total_usd is not None]
        total = sum(known, start=Decimal(0)) if known else None
        priced = all(call.priced for call in group)
        budget = config.model_budgets.get(name)
        over = budget is not None and total is not None and total > budget
        if over and budget is not None and total is not None:
            blocked = True
            reasons.append(
                f"{name} is estimated at {_usd(total)} USD, "
                f"which is over its {_usd(budget)} USD budget."
            )
        elif budget is not None and not priced:
            reasons.append(
                f"{name} has a budget, but at least one call has no price, "
                "so the budget was only checked against known dollars."
            )
        models.append(
            ModelEstimate(
                model_name=name,
                calls=group,
                total_usd=total,
                budget_usd=budget,
                priced=priced,
                over_budget=over,
            )
        )

    known_models = [model.total_usd for model in models if model.total_usd is not None]
    total = sum(known_models, start=Decimal(0)) if known_models else None
    total_budget = config.total_budget_usd
    if total_budget is not None and total is not None and total > total_budget:
        blocked = True
        reasons.append(
            f"The project total is {_usd(total)} USD, "
            f"which is over the {_usd(total_budget)} USD budget."
        )
    uncertain = any(not call.priced for call in calls)
    if uncertain:
        reasons.append(
            "Some calls have no published price. They are left unpriced "
            "rather than counted as 0 USD."
        )
    return EstimateResult(
        models=models,
        calls=calls,
        total_usd=total,
        total_budget_usd=total_budget,
        blocked=blocked,
        uncertain=uncertain,
        reasons=reasons,
    )


def _line_cost(rows: int, tokens: int, rate_per_million: Decimal) -> Decimal:
    raw = Decimal(rows) * Decimal(tokens) * rate_per_million / _MILLION
    return raw.quantize(_CENT, rounding=ROUND_HALF_UP)


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _usd(value: Decimal) -> str:
    text = format(value.quantize(_CENT, rounding=ROUND_HALF_UP), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
