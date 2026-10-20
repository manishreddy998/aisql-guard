"""Load aisql-guard.toml, or the tool.aisql-guard table in pyproject.toml."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

_SEVERITIES = {"note", "warning", "error"}


class ConfigError(ValueError):
    """The config file was found, but a value in it is not usable."""


@dataclass(frozen=True)
class ModelAssumption:
    """Per model row count and token assumptions. Unset fields use defaults."""

    rows: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    model: str | None = None


@dataclass(frozen=True)
class ResolvedAssumption:
    """A model assumption with every count filled in."""

    rows: int
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    model: str | None = None


@dataclass(frozen=True)
class LiveSample:
    """SQL used only when live mode is turned on for a run."""

    prompt: str | None = None
    source: str | None = None
    where: str | None = None
    count_tokens_sql: str | None = None
    dry_run_sql: str | None = None


@dataclass(frozen=True)
class GuardConfig:
    """Everything the linter, the estimate, and the budget gate read."""

    dialect: str = "bigquery"
    disabled_rules: frozenset[str] = field(default_factory=frozenset)
    fail_on: str = "error"
    default_model: str = "google-3.5-flash-lite"
    default_rows: int = 1000
    default_input_tokens: int = 500
    default_output_tokens: int = 128
    default_thinking_tokens: int = 0
    snowflake_ai_credit_usd: Decimal = Decimal("2.00")
    databricks_usd_per_dbu: Decimal = Decimal("0.07")
    models: dict[str, ModelAssumption] = field(default_factory=dict)
    total_budget_usd: Decimal | None = None
    model_budgets: dict[str, Decimal] = field(default_factory=dict)
    live_project: str | None = None
    live_location: str | None = None
    live_sample_rows: int = 20
    live_models: dict[str, LiveSample] = field(default_factory=dict)
    path: Path | None = None

    def assumption_for(self, model_name: str | None) -> ResolvedAssumption:
        """Fill a model's blanks from the project defaults."""

        raw = self.models.get(model_name or "", ModelAssumption())
        return ResolvedAssumption(
            rows=self.default_rows if raw.rows is None else raw.rows,
            input_tokens=(
                self.default_input_tokens if raw.input_tokens is None else raw.input_tokens
            ),
            output_tokens=(
                self.default_output_tokens if raw.output_tokens is None else raw.output_tokens
            ),
            thinking_tokens=(
                self.default_thinking_tokens
                if raw.thinking_tokens is None
                else raw.thinking_tokens
            ),
            model=raw.model,
        )


def load_config(start: Path | None = None, explicit: Path | None = None) -> GuardConfig:
    """Find and parse config.

    An explicit path is required to exist. Otherwise the search walks up from
    ``start`` for ``aisql-guard.toml``. If that file never appears, the same
    walk looks for a ``[tool.aisql-guard]`` table. No file means defaults.
    """

    if explicit is not None:
        path = explicit.expanduser()
        if not path.is_file():
            raise ConfigError(f"config file not found: {explicit}")
        return _from_file(path.resolve())

    start_dir = Path.cwd() if start is None else start
    if start_dir.is_file():
        start_dir = start_dir.parent
    start_dir = start_dir.resolve()
    directories = [start_dir, *start_dir.parents]
    for directory in directories:
        candidate = directory / "aisql-guard.toml"
        if candidate.is_file():
            return _from_file(candidate)
    for directory in directories:
        pyproject = directory / "pyproject.toml"
        if not pyproject.is_file():
            continue
        data = _read_toml(pyproject)
        tool = data.get("tool")
        if isinstance(tool, dict) and "aisql-guard" in tool:
            section = tool["aisql-guard"]
            if not isinstance(section, dict):
                raise ConfigError("tool.aisql-guard must be a table")
            return _from_mapping(cast(dict[str, Any], section), path=pyproject)
    return GuardConfig()


def _from_file(path: Path) -> GuardConfig:
    data = _read_toml(path)
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a table")
    return _from_mapping(data, path=path)


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        loaded = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"could not parse {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path} must be a table")
    return cast(dict[str, Any], loaded)


def _from_mapping(data: dict[str, Any], *, path: Path | None) -> GuardConfig:
    rules = _table(data.get("rules"), "rules")
    estimate = _table(data.get("estimate"), "estimate")
    budgets = _table(data.get("budgets"), "budgets")
    live = _table(data.get("live"), "live")
    fail_on = str(rules.get("fail_on", "error"))
    if fail_on not in _SEVERITIES:
        raise ConfigError("rules.fail_on must be note, warning, or error")
    disabled = rules.get("disable") or []
    if not isinstance(disabled, list) or not all(isinstance(item, str) for item in disabled):
        raise ConfigError("rules.disable must be a list of rule ids")
    return GuardConfig(
        dialect=str(data.get("dialect", "bigquery")),
        disabled_rules=frozenset(cast(list[str], disabled)),
        fail_on=fail_on,
        default_model=str(estimate.get("default_model", "google-3.5-flash-lite")),
        default_rows=_int(estimate.get("default_rows", 1000), "estimate.default_rows"),
        default_input_tokens=_int(
            estimate.get("default_input_tokens", 500), "estimate.default_input_tokens"
        ),
        default_output_tokens=_int(
            estimate.get("default_output_tokens", 128), "estimate.default_output_tokens"
        ),
        default_thinking_tokens=_int(
            estimate.get("default_thinking_tokens", 0), "estimate.default_thinking_tokens"
        ),
        snowflake_ai_credit_usd=_decimal(
            estimate.get("snowflake_ai_credit_usd", "2.00"),
            "estimate.snowflake_ai_credit_usd",
        ),
        databricks_usd_per_dbu=_decimal(
            estimate.get("databricks_usd_per_dbu", "0.07"),
            "estimate.databricks_usd_per_dbu",
        ),
        models=_model_assumptions(estimate.get("models")),
        total_budget_usd=_optional_decimal(budgets.get("total_usd"), "budgets.total_usd"),
        model_budgets=_budget_map(budgets.get("models")),
        live_project=_optional_text(live.get("project")),
        live_location=_optional_text(live.get("location")),
        live_sample_rows=_int(live.get("sample_rows", 20), "live.sample_rows"),
        live_models=_live_models(live.get("models")),
        path=path,
    )


def _model_assumptions(raw: object) -> dict[str, ModelAssumption]:
    table = _table(raw, "estimate.models")
    models: dict[str, ModelAssumption] = {}
    for name, body in table.items():
        fields = _table(body, f"estimate.models.{name}")
        models[str(name)] = ModelAssumption(
            rows=_optional_int(fields.get("rows"), f"estimate.models.{name}.rows"),
            input_tokens=_optional_int(
                fields.get("input_tokens"), f"estimate.models.{name}.input_tokens"
            ),
            output_tokens=_optional_int(
                fields.get("output_tokens"), f"estimate.models.{name}.output_tokens"
            ),
            thinking_tokens=_optional_int(
                fields.get("thinking_tokens"), f"estimate.models.{name}.thinking_tokens"
            ),
            model=_optional_text(fields.get("model")),
        )
    return models


def _budget_map(raw: object) -> dict[str, Decimal]:
    table = _table(raw, "budgets.models")
    budgets: dict[str, Decimal] = {}
    for name, value in table.items():
        parsed = _optional_decimal(value, f"budgets.models.{name}")
        if parsed is None:
            raise ConfigError(f"budgets.models.{name} is required")
        budgets[str(name)] = parsed
    return budgets


def _live_models(raw: object) -> dict[str, LiveSample]:
    table = _table(raw, "live.models")
    samples: dict[str, LiveSample] = {}
    for name, body in table.items():
        fields = _table(body, f"live.models.{name}")
        samples[str(name)] = LiveSample(
            prompt=_optional_text(fields.get("prompt")),
            source=_optional_text(fields.get("source")),
            where=_optional_text(fields.get("where")),
            count_tokens_sql=_optional_text(fields.get("count_tokens_sql")),
            dry_run_sql=_optional_text(fields.get("dry_run_sql")),
        )
    return samples


def _table(value: object, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a table")
    return cast(dict[str, Any], value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: object, label: str) -> int:
    parsed = _optional_int(value, label)
    if parsed is None:
        raise ConfigError(f"{label} is required")
    return parsed


def _optional_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{label} must be a whole number")
    if value < 0:
        raise ConfigError(f"{label} cannot be negative")
    return value


def _optional_decimal(value: object, label: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ConfigError(f"{label} must be a decimal")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ConfigError(f"{label} must be a decimal") from exc
    if parsed < 0:
        raise ConfigError(f"{label} cannot be negative")
    return parsed


def _decimal(value: object, label: str) -> Decimal:
    parsed = _optional_decimal(value, label)
    if parsed is None:
        raise ConfigError(f"{label} is required")
    return parsed
