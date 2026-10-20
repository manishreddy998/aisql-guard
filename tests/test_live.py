"""Live mode is opt-in and the tests never open a real BigQuery client."""

from __future__ import annotations

import builtins
import sys
import types
from decimal import Decimal

import pytest

from aisql_guard.config import GuardConfig, LiveSample
from aisql_guard.estimate import estimate_calls, estimate_paths
from aisql_guard.live import BigQueryProbe, build_count_tokens_sql, connect_bigquery
from aisql_guard.models import AiCall


class FakeProbe:
    """Stand-in for BigQuery. Records SQL and returns fixed numbers."""

    def __init__(self) -> None:
        self.dry: list[str] = []
        self.samples: list[str] = []

    def dry_run_bytes(self, sql: str) -> int:
        self.dry.append(sql)
        return 4096

    def average_input_tokens(self, sql: str) -> float:
        self.samples.append(sql)
        return 250.6


def _call() -> AiCall:
    return AiCall(
        name="AI.GENERATE",
        provider="bigquery",
        path="batch.sql",
        line=1,
        column=1,
        model_name="batch",
        model_hint=None,
        has_output_cap=True,
        output_token_cap=100,
        has_thinking_budget=True,
        thinking_token_cap=0,
        needs_output_cap=True,
        needs_thinking_budget=True,
    )


def _config() -> GuardConfig:
    return GuardConfig(
        default_model="google-3.5-flash-lite",
        default_rows=1000,
        default_input_tokens=1000,
        default_output_tokens=100,
        default_thinking_tokens=0,
        live_project="billing-lab",
        live_location="US",
        live_sample_rows=20,
        live_models={
            "batch": LiveSample(
                prompt="body",
                source="`warehouse.tickets`",
                where="status = 'open'",
                dry_run_sql="select body from `warehouse.tickets` where status = 'open'",
            )
        },
    )


def test_count_tokens_sql_limits_the_inner_query() -> None:
    sample = LiveSample(prompt="body", source="`warehouse.tickets`", where="status = 'open'")
    sql = build_count_tokens_sql(sample, 20)
    assert "AI.COUNT_TOKENS(body)" in sql
    assert "limit 20)" in sql
    assert sql.index("limit 20") < sql.rindex(")")
    assert "avg(token_count)" in sql
    custom = build_count_tokens_sql(
        LiveSample(count_tokens_sql="select 1 as token_count"),
        20,
    )
    assert custom == "select 1 as token_count"
    with pytest.raises(ValueError, match="prompt and source"):
        build_count_tokens_sql(LiveSample(), 20)
    with pytest.raises(ValueError, match="sample_rows"):
        build_count_tokens_sql(sample, 0)
    plain = build_count_tokens_sql(LiveSample(prompt="body", source="tickets"), 5)
    assert " where " not in plain
    assert plain.endswith("limit 5)")


def test_offline_estimate_never_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> FakeProbe:
        raise AssertionError("offline estimate tried to connect")

    monkeypatch.setattr("aisql_guard.live.connect_bigquery", boom)
    probe = FakeProbe()
    result = estimate_calls([_call()], _config(), live=False, probe=probe)
    assert result.calls[0].input_tokens == 1000
    assert probe.samples == []
    assert probe.dry == []
    paths = estimate_paths([], _config(), live=False, probe=probe)
    assert paths.calls == []


def test_live_probe_replaces_input_tokens_and_keeps_bytes_out_of_the_dollars() -> None:
    probe = FakeProbe()
    result = estimate_calls([_call()], _config(), live=True, probe=probe)
    call = result.calls[0]
    assert call.input_tokens == 251
    assert call.input_usd == Decimal("0.0753")
    assert call.dry_run_bytes == 4096
    assert "AI.COUNT_TOKENS(body)" in probe.samples[0]
    assert "limit 20)" in probe.samples[0]
    assert probe.dry == ["select body from `warehouse.tickets` where status = 'open'"]
    assert any("billed separately" in note for note in call.notes)
    assert call.total_usd == call.input_usd + call.output_usd + call.thinking_usd


def test_live_without_a_probe_uses_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = FakeProbe()
    seen: dict[str, str | None] = {}

    def connect(project: str | None, location: str | None) -> FakeProbe:
        seen["project"] = project
        seen["location"] = location
        return probe

    monkeypatch.setattr("aisql_guard.live.connect_bigquery", connect)
    config = GuardConfig(
        live_project="billing-lab",
        live_location="US",
        live_models={"batch": LiveSample(count_tokens_sql="select 3 as token_count")},
    )
    result = estimate_calls([_call()], config, live=True)
    assert seen == {"project": "billing-lab", "location": "US"}
    assert probe.samples == ["select 3 as token_count"]
    assert result.calls[0].input_tokens == 251
    assert any("configured AI.COUNT_TOKENS" in note for note in result.calls[0].notes)


def test_two_calls_in_one_model_share_a_sample() -> None:
    probe = FakeProbe()
    second = _call()
    result = estimate_calls([_call(), second], _config(), live=True, probe=probe)
    assert len(probe.samples) == 1
    assert len(probe.dry) == 1
    assert result.calls[0].input_tokens == result.calls[1].input_tokens == 251


def test_live_without_sample_sql_keeps_the_assumption() -> None:
    probe = FakeProbe()
    result = estimate_calls([_call()], GuardConfig(), live=True, probe=probe)
    assert probe.samples == []
    assert result.calls[0].input_tokens == 500
    assert any("no sample SQL" in note for note in result.calls[0].notes)


def test_connect_requires_the_live_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def blocked(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "google.cloud" or name.startswith("google.cloud"):
            raise ImportError("blocked for the test")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(RuntimeError, match="optional dependency"):
        connect_bigquery("billing-lab", "US")


def test_connect_uses_an_injected_client(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, str | None] = {}

    class Client:
        def __init__(self, project: str | None = None, location: str | None = None) -> None:
            created["project"] = project
            created["location"] = location

    google = types.ModuleType("google")
    cloud = types.ModuleType("google.cloud")
    bigquery = types.ModuleType("google.cloud.bigquery")
    bigquery.Client = Client  # type: ignore[attr-defined]
    cloud.bigquery = bigquery  # type: ignore[attr-defined]
    google.cloud = cloud  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bigquery)
    probe = connect_bigquery("billing-lab", "US")
    assert isinstance(probe, BigQueryProbe)
    assert created == {"project": "billing-lab", "location": "US"}


def test_probe_methods_use_the_client_and_not_the_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Config:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr("aisql_guard.live._query_job_config", lambda **kwargs: Config(**kwargs))

    class Job:
        def __init__(self, sql: str, job_config: Config | None) -> None:
            self.sql = sql
            self.job_config = job_config
            self.total_bytes_processed = 99

        def result(self) -> list[dict[str, float]]:
            return [{"token_count": 12.5}]

    class Client:
        def __init__(self) -> None:
            self.jobs: list[Job] = []

        def query(self, sql: str, job_config: Config | None = None) -> Job:
            job = Job(sql, job_config)
            self.jobs.append(job)
            return job

    client = Client()
    probe = BigQueryProbe(client, location="US")
    assert probe.dry_run_bytes("select 1") == 99
    assert client.jobs[0].job_config is not None
    assert client.jobs[0].job_config.kwargs["dry_run"] is True
    assert probe.average_input_tokens("select avg(token_count)") == 12.5

    class EmptyClient:
        def query(self, sql: str, job_config: object = None) -> object:
            class EmptyJob:
                def result(self) -> list[object]:
                    return []

            return EmptyJob()

    with pytest.raises(RuntimeError, match="no rows"):
        BigQueryProbe(EmptyClient()).average_input_tokens("select 1")


def test_token_row_can_be_a_mapping_without_a_positional_index() -> None:
    class Row:
        def __getitem__(self, key: object) -> int:
            if key == 0:
                raise TypeError("no positional index")
            if key == "token_count":
                return 8
            raise KeyError(key)

        def keys(self) -> list[str]:
            return ["token_count"]

    from aisql_guard.live import _first_cell

    assert _first_cell(Row()) == 8.0
    assert _first_cell({"token_count": 4}) == 4.0
    with pytest.raises(RuntimeError, match="empty row"):
        _first_cell(RowEmpty())


class RowEmpty:
    def __getitem__(self, key: object) -> int:
        raise TypeError("no positional index")

    def keys(self) -> list[str]:
        return []
