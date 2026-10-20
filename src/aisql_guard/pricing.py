"""Published token prices used by the offline estimate.

Rates are copied from the vendor pages named in ``pricing.json``. They are
not guessed. USD for Snowflake is credits times the AI credit price. USD for
Databricks is DBU times the SKU rate. Thinking tokens are billed at the
output rate, because the Google Cloud output row covers response and
reasoning tokens.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from importlib.resources import files

_SPACE = re.compile(r"\s+")
_DASH = re.compile(r"-+")


@dataclass(frozen=True)
class PriceSource:
    """A page the rates were copied from, and the day they were read."""

    id: str
    title: str
    url: str
    retrieved: str
    notes: str


@dataclass(frozen=True)
class ModelPrice:
    """One priced model. Rates are per million tokens, in ``unit``."""

    id: str
    provider: str
    unit: str
    input_per_million: Decimal
    output_per_million: Decimal
    page_row: str
    source: str
    note: str = ""

    def usd_rates(
        self,
        *,
        snowflake_credit_usd: Decimal,
        databricks_usd_per_dbu: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Return input and output USD per million tokens."""

        if self.unit == "usd":
            factor = Decimal(1)
        elif self.unit == "credits":
            factor = snowflake_credit_usd
        elif self.unit == "dbu":
            factor = databricks_usd_per_dbu
        else:
            raise ValueError(f"unknown price unit {self.unit}")
        return self.input_per_million * factor, self.output_per_million * factor


@dataclass(frozen=True)
class PriceCatalog:
    """The versioned table shipped with the package."""

    version: int
    retrieved: str
    sources: tuple[PriceSource, ...]
    models: tuple[ModelPrice, ...]
    snowflake_ai_credit_usd: Decimal
    snowflake_ai_credit_usd_regional: Decimal
    databricks_usd_per_dbu: Decimal
    _by_key: dict[str, tuple[ModelPrice, ...]]

    def get(self, model_id: str) -> ModelPrice | None:
        """Return a model by its canonical id."""

        found = self._by_key.get(_normalize(model_id), ())
        for price in found:
            if price.id == model_id or _normalize(price.id) == _normalize(model_id):
                return price
        return found[0] if len(found) == 1 else None

    def resolve(self, name: str, *, provider: str | None = None) -> ModelPrice | None:
        """Match a model id, alias, or page label.

        A remote model path that is not in the table returns None so the
        caller can try the configured model id next. When one label is used
        by two providers, ``provider`` picks the row.
        """

        found = self._by_key.get(_normalize(name), ())
        if not found:
            return None
        if provider is not None:
            for price in found:
                if price.provider == provider:
                    return price
        if len(found) == 1:
            return found[0]
        return None

    def source(self, source_id: str) -> PriceSource | None:
        for item in self.sources:
            if item.id == source_id:
                return item
        return None


def _normalize(value: str) -> str:
    text = value.strip().lower().replace("_", "-")
    text = _SPACE.sub("-", text)
    text = _DASH.sub("-", text)
    return text


def _decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"{label} must be a decimal string")
    return Decimal(value)


@lru_cache(maxsize=1)
def load_pricing() -> PriceCatalog:
    """Load the price table packaged next to this module."""

    raw = files("aisql_guard").joinpath("data/pricing.json").read_text(encoding="utf-8")
    return catalog_from_json(raw)


def catalog_from_json(raw: str) -> PriceCatalog:
    """Parse a price table. Used by tests that want a private copy."""

    data = json.loads(raw)
    sources = tuple(
        PriceSource(
            id=item["id"],
            title=item["title"],
            url=item["url"],
            retrieved=item["retrieved"],
            notes=item.get("notes", ""),
        )
        for item in data["sources"]
    )
    source_ids = {item.id for item in sources}
    models: list[ModelPrice] = []
    index: dict[str, list[ModelPrice]] = {}
    for item in data["models"]:
        if item["source"] not in source_ids:
            raise ValueError(f"{item['id']} cites an unknown source")
        price = ModelPrice(
            id=item["id"],
            provider=item["provider"],
            unit=item["unit"],
            input_per_million=_decimal(item["input_per_million"], item["id"]),
            output_per_million=_decimal(item["output_per_million"], item["id"]),
            page_row=item["page_row"],
            source=item["source"],
            note=item.get("note", ""),
        )
        models.append(price)
        labels = [price.id, price.page_row, *item.get("aliases", [])]
        for label in labels:
            key = _normalize(label)
            bucket = index.setdefault(key, [])
            clash = any(
                existing.id != price.id and existing.provider == price.provider
                for existing in bucket
            )
            if clash:
                raise ValueError(f"price label {label} is used by two {price.provider} models")
            if all(existing.id != price.id for existing in bucket):
                bucket.append(price)
    multipliers = data["multipliers"]
    return PriceCatalog(
        version=int(data["version"]),
        retrieved=data["retrieved"],
        sources=sources,
        models=tuple(models),
        snowflake_ai_credit_usd=_decimal(
            multipliers["snowflake_ai_credit_usd"], "snowflake_ai_credit_usd"
        ),
        snowflake_ai_credit_usd_regional=_decimal(
            multipliers["snowflake_ai_credit_usd_regional"],
            "snowflake_ai_credit_usd_regional",
        ),
        databricks_usd_per_dbu=_decimal(
            multipliers["databricks_usd_per_dbu"], "databricks_usd_per_dbu"
        ),
        _by_key={key: tuple(bucket) for key, bucket in index.items()},
    )
