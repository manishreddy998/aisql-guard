"""Reports stay stable for text, JSON, and SARIF."""

from __future__ import annotations

import json
from pathlib import Path

from aisql_guard.config import load_config
from aisql_guard.engine import lint_paths
from aisql_guard.estimate import estimate_paths
from aisql_guard.reporters import Report, render

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "examples" / "support_tickets"


def _report() -> Report:
    config = load_config(PROJECT)
    linted = lint_paths([PROJECT], dialect=config.dialect)
    estimate = estimate_paths([PROJECT], config)
    return Report(
        findings=linted.findings,
        estimate=estimate,
        fail_on=config.fail_on,
        config_path="aisql-guard.toml",
    )


def test_text_names_rules_and_the_blocked_budget() -> None:
    text = render(_report(), "text")
    assert "aisql-guard 0.1.0" in text
    assert "ASG001" in text
    assert "ASG004" in text
    assert "ticket_summaries_risky" in text
    assert "1264.5" in text
    assert "result: blocked" in text
    assert "\x1b" not in text


def test_json_matches_the_objects() -> None:
    report = _report()
    payload = json.loads(render(report, "json"))
    assert payload["version"] == "0.1.0"
    assert payload["failed"] is True
    assert payload["budget_blocked"] is True
    assert payload["estimate"]["blocked"] is True
    assert {item["rule_id"] for item in payload["findings"]} >= {
        "ASG001",
        "ASG002",
        "ASG003",
        "ASG004",
        "ASG005",
        "ASG006",
        "ASG007",
    }


def test_sarif_points_at_the_source_line() -> None:
    document = json.loads(render(_report(), "sarif"))
    assert document["version"] == "2.1.0"
    run = document["runs"][0]
    assert run["tool"]["driver"]["name"] == "aisql-guard"
    results = run["results"]
    generate = next(item for item in results if item["ruleId"] == "ASG001")
    region = generate["locations"][0]["physicalLocation"]["region"]
    assert region["startLine"] >= 1
    assert region["startColumn"] >= 1
    budget = [item for item in results if item["ruleId"] == "ASG100"]
    assert budget
    assert budget[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == (
        "aisql-guard.toml"
    )
    rule_ids = {rule["id"] for rule in run["tool"]["driver"]["rules"]}
    assert "ASG001" in rule_ids
    assert "ASG100" in rule_ids
    assert all(rule["help"]["text"] for rule in run["tool"]["driver"]["rules"])


def test_clean_scan_is_ok() -> None:
    linted = lint_paths([PROJECT / "models" / "marts" / "capped_batch.sql"])
    report = Report(findings=linted.findings, estimate=None, fail_on="error", show_estimate=False)
    text = render(report, "text")
    assert "no findings" in text
    assert "result: ok" in text
    assert report.failed is False


def test_warning_does_not_fail_when_the_gate_is_error() -> None:
    linted = lint_paths([PROJECT / "models" / "marts" / "star_prompt.sql"])
    report = Report(findings=linted.findings, estimate=None, fail_on="error", show_estimate=False)
    assert any(item.rule_id == "ASG007" for item in report.findings)
    assert report.failed is False
    strict = Report(findings=linted.findings, estimate=None, fail_on="warning", show_estimate=False)
    assert strict.failed is True
    assert "result: failed" in render(strict, "text")


def test_unknown_format_is_rejected() -> None:
    report = Report(findings=[], estimate=None, fail_on="error", show_estimate=False)
    try:
        render(report, "html")
    except ValueError as exc:
        assert "unknown format" in str(exc)
    else:
        raise AssertionError("expected ValueError")
