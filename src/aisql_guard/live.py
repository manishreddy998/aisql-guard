"""Optional BigQuery dry run and input token sample.

Live mode stays off unless the caller passes ``live=True``. Importing this
module does not contact Google Cloud. ``AI.COUNT_TOKENS`` counts input only,
so the sample never prices output or thinking tokens. See
https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-count-tokens
"""

from __future__ import annotations

from typing import Any, Protocol

from aisql_guard.config import LiveSample


class LiveProbe(Protocol):
    """The two BigQuery reads a live estimate is allowed to make."""

    def dry_run_bytes(self, sql: str) -> int:
        """Return bytes processed for a dry run. Do not execute the query."""

    def average_input_tokens(self, sql: str) -> float:
        """Return the average input token count for a sample query."""


class BigQueryProbe:
    """A probe backed by an already constructed BigQuery client."""

    def __init__(self, client: Any, location: str | None = None) -> None:
        self._client = client
        self._location = location

    def dry_run_bytes(self, sql: str) -> int:
        job_config = _query_job_config(dry_run=True, use_query_cache=False)
        job = self._client.query(sql, job_config=job_config)
        return int(job.total_bytes_processed)

    def average_input_tokens(self, sql: str) -> float:
        job = self._client.query(sql)
        rows = list(job.result())
        if not rows:
            raise RuntimeError("AI.COUNT_TOKENS sample returned no rows")
        return _first_cell(rows[0])


def connect_bigquery(project: str | None, location: str | None) -> BigQueryProbe:
    """Open a client. Called only when a run explicitly asks for live mode."""

    try:
        from google.cloud import bigquery
    except ImportError as exc:
        raise RuntimeError(
            "Live mode needs the optional dependency. "
            "Install it with pip install 'aisql-guard[live]'."
        ) from exc
    client = bigquery.Client(project=project, location=location)
    return BigQueryProbe(client, location=location)


def build_count_tokens_sql(sample: LiveSample, sample_rows: int) -> str:
    """Build a sample query whose LIMIT is inside the aggregate.

    ``select avg(AI.COUNT_TOKENS(prompt)) from t limit 20`` would still read
    every row, because the limit is applied after the aggregate. The limit
    belongs on the inner query.
    """

    if sample.count_tokens_sql:
        return sample.count_tokens_sql
    if not sample.prompt or not sample.source:
        raise ValueError("a live sample needs count_tokens_sql, or both prompt and source")
    if sample_rows < 1:
        raise ValueError("sample_rows must be at least 1")
    where = f" where {sample.where}" if sample.where else ""
    return (
        "select avg(token_count) from ("
        f"select AI.COUNT_TOKENS({sample.prompt}).result as token_count "
        f"from {sample.source}{where} "
        f"limit {sample_rows})"
    )


def _query_job_config(**kwargs: Any) -> Any:
    from google.cloud import bigquery

    return bigquery.QueryJobConfig(**kwargs)


def _first_cell(row: Any) -> float:
    if isinstance(row, dict):
        value = next(iter(row.values()))
        return float(value)
    try:
        return float(row[0])
    except (KeyError, IndexError, TypeError, ValueError):
        keys = list(row.keys())
        if not keys:
            raise RuntimeError("AI.COUNT_TOKENS sample returned an empty row") from None
        return float(row[keys[0]])
