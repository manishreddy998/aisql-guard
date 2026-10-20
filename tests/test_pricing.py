"""The price table is copied from public pages, not invented."""

from __future__ import annotations

from decimal import Decimal

from aisql_guard.pricing import load_pricing


def test_catalog_cites_sources_and_a_retrieved_date() -> None:
    catalog = load_pricing()
    assert catalog.version == 1
    assert catalog.retrieved == "2026-10-20"
    assert catalog.snowflake_ai_credit_usd == Decimal("2.00")
    assert catalog.snowflake_ai_credit_usd_regional == Decimal("2.20")
    assert catalog.databricks_usd_per_dbu == Decimal("0.07")
    urls = {item.url for item in catalog.sources}
    assert "https://cloud.google.com/vertex-ai/generative-ai/pricing" in urls
    assert "https://www.snowflake.com/legal-files/CreditConsumptionTable.pdf" in urls
    assert "https://www.databricks.com/product/pricing/foundation-model-serving" in urls
    for source in catalog.sources:
        assert source.retrieved == "2026-10-20"
        assert source.url.startswith("https://")
    for price in catalog.models:
        assert catalog.source(price.source) is not None


def test_google_text_rates_match_the_page() -> None:
    catalog = load_pricing()
    lite = catalog.get("google-3.5-flash-lite")
    assert lite is not None
    assert lite.input_per_million == Decimal("0.30")
    assert lite.output_per_million == Decimal("2.50")
    flash = catalog.get("google-3.5-flash")
    assert flash is not None
    assert (flash.input_per_million, flash.output_per_million) == (
        Decimal("1.50"),
        Decimal("9.00"),
    )
    intro = catalog.get("google-3.8-flash")
    assert intro is not None
    assert intro.input_per_million == Decimal("0.75")
    assert "2026-12-31" in intro.note
    assert "7.50" in intro.note
    older = catalog.get("google-2.5-flash")
    assert older is not None
    assert older.input_per_million == Decimal("0.30")
    assert "1.00" in older.note


def test_aliases_resolve_and_unknown_names_do_not() -> None:
    catalog = load_pricing()
    assert catalog.resolve("3.5 Flash-Lite", provider="bigquery") is catalog.get(
        "google-3.5-flash-lite"
    )
    assert catalog.resolve("llama-3.1-8b", provider="snowflake") is catalog.get("llama3.1-8b")
    assert catalog.resolve("Llama 3.3 70B", provider="databricks") is catalog.get("llama-3-3-70b")
    assert catalog.resolve("warehouse.remote_models.ticket_model") is None
    assert catalog.resolve("not-a-priced-model", provider="bigquery") is None


def test_snowflake_and_databricks_stay_in_vendor_units() -> None:
    catalog = load_pricing()
    small = catalog.get("llama3.1-8b")
    assert small is not None
    assert small.unit == "credits"
    assert small.input_per_million == Decimal("0.132")
    input_usd, output_usd = small.usd_rates(
        snowflake_credit_usd=catalog.snowflake_ai_credit_usd,
        databricks_usd_per_dbu=catalog.databricks_usd_per_dbu,
    )
    assert input_usd == Decimal("0.264")
    assert output_usd == Decimal("0.264")

    maverick = catalog.get("llama-4-maverick")
    assert maverick is not None
    assert maverick.unit == "dbu"
    input_dbu, output_dbu = maverick.usd_rates(
        snowflake_credit_usd=Decimal("2.00"),
        databricks_usd_per_dbu=Decimal("0.07"),
    )
    assert input_dbu == Decimal("0.50001")
    assert output_dbu == Decimal("1.50003")


def test_same_label_can_differ_by_provider() -> None:
    catalog = load_pricing()
    snow = catalog.resolve("llama4-maverick", provider="snowflake")
    bricks = catalog.resolve("llama-4-maverick", provider="databricks")
    assert snow is not None and bricks is not None
    assert snow.unit == "credits"
    assert bricks.unit == "dbu"
    assert snow.id != bricks.id
