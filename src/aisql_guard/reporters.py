"""Text, JSON, and SARIF reports."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from decimal import Decimal

from rich.console import Console
from rich.table import Table
from rich.text import Text

from aisql_guard import __version__
from aisql_guard.estimate import EstimateResult
from aisql_guard.models import Finding
from aisql_guard.rules import RULES, SEVERITY_RANK

_STYLES = {"error": "red", "warning": "yellow", "note": "cyan"}


@dataclass
class Report:
    """Lint findings and, when requested, the cost estimate."""

    findings: list[Finding]
    estimate: EstimateResult | None
    fail_on: str
    show_findings: bool = True
    show_estimate: bool = True
    config_path: str | None = None

    @property
    def finding_failed(self) -> bool:
        if not self.show_findings:
            return False
        minimum = SEVERITY_RANK[self.fail_on]
        return any(SEVERITY_RANK[item.severity] >= minimum for item in self.findings)

    @property
    def budget_blocked(self) -> bool:
        return self.show_estimate and self.estimate is not None and self.estimate.blocked

    @property
    def failed(self) -> bool:
        return self.finding_failed or self.budget_blocked


def render(report: Report, fmt: str, *, color: bool = False, width: int = 96) -> str:
    """Render a report. ``fmt`` is text, json, or sarif."""

    if fmt == "json":
        return render_json(report)
    if fmt == "sarif":
        return render_sarif(report)
    if fmt == "text":
        return render_text(report, color=color, width=width)
    raise ValueError(f"unknown format {fmt}")


def render_text(report: Report, *, color: bool = False, width: int = 96) -> str:
    """Human readable report. Color is omitted when ``color`` is false."""

    console = _console(color=color, width=width)
    _fill_text(console, report)
    return console.export_text(clear=False)


def render_text_svg(report: Report, *, width: int = 96) -> str:
    """Same text report as an SVG, for the docs screenshots."""

    console = _console(color=True, width=width)
    _fill_text(console, report)
    return console.export_svg(title="aisql-guard")


def render_json(report: Report) -> str:
    """Machine readable report."""

    payload = {
        "version": __version__,
        "findings": [item.to_dict() for item in report.findings] if report.show_findings else [],
        "estimate": report.estimate.to_dict() if report.show_estimate and report.estimate else None,
        "budget_blocked": report.budget_blocked,
        "failed": report.failed,
    }
    return json.dumps(payload, indent=2) + "\n"


def render_sarif(report: Report) -> str:
    """SARIF 2.1.0 for GitHub code scanning."""

    rules = []
    seen: set[str] = set()
    results = []
    if report.show_findings:
        for finding in report.findings:
            seen.add(finding.rule_id)
            results.append(_finding_result(finding))
    if report.budget_blocked and report.estimate is not None:
        seen.add("ASG100")
        for reason in report.estimate.reasons:
            if "over" not in reason:
                continue
            results.append(_budget_result(reason, report.config_path))
    for rule_id in sorted(seen):
        rule = RULES.get(rule_id)
        if rule is None:
            continue
        rules.append(
            {
                "id": rule.id,
                "name": rule.name,
                "shortDescription": {"text": rule.summary},
                "fullDescription": {"text": rule.summary},
                "help": {"text": rule.fix},
                "defaultConfiguration": {"level": _sarif_level(rule.severity)},
            }
        )
    document = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "aisql-guard",
                        "version": __version__,
                        "informationUri": "https://github.com/manishreddy998/aisql-guard",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(document, indent=2) + "\n"


def _console(*, color: bool, width: int) -> Console:
    # Record into a buffer. The caller prints the exported text once.
    return Console(
        file=io.StringIO(),
        record=True,
        width=width,
        force_terminal=color,
        no_color=not color,
        color_system="standard" if color else None,
        highlight=False,
    )


def _fill_text(console: Console, report: Report) -> None:
    title = Text(f"aisql-guard {__version__}")
    title.stylize("bold")
    console.print(title)
    if report.show_findings:
        console.print()
        if not report.findings:
            console.print("no findings")
        for finding in report.findings:
            head = Text()
            head.append(f"{finding.path}:{finding.line}:{finding.column}  ")
            head.append(finding.rule_id, style=_STYLES.get(finding.severity, "white"))
            head.append(f"  {finding.severity}")
            console.print(head)
            console.print(f"  {finding.message}")
            console.print(f"  {finding.fix}")
    if report.show_estimate and report.estimate is not None:
        console.print()
        console.print(_estimate_table(report.estimate))
        for reason in report.estimate.reasons:
            console.print(reason)
    console.print()
    if report.budget_blocked:
        console.print("result: blocked")
    elif report.finding_failed:
        console.print("result: failed")
    else:
        console.print("result: ok")


def _estimate_table(estimate: EstimateResult) -> Table:
    table = Table(title="estimate", expand=True)
    table.add_column("model")
    table.add_column("usd", justify="right")
    table.add_column("budget", justify="right")
    table.add_column("status")
    for model in estimate.models:
        if model.over_budget:
            status = "over"
        elif not model.priced:
            status = "unpriced"
        else:
            status = "ok"
        table.add_row(
            model.model_name,
            _usd(model.total_usd),
            _usd(model.budget_usd),
            status,
        )
    table.add_row("total", _usd(estimate.total_usd), _usd(estimate.total_budget_usd), "")
    return table


def _finding_result(finding: Finding) -> dict[str, object]:
    return {
        "ruleId": finding.rule_id,
        "level": _sarif_level(finding.severity),
        "message": {"text": f"{finding.message} {finding.fix}"},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": finding.path.replace("\\", "/")},
                    "region": {
                        "startLine": max(finding.line, 1),
                        "startColumn": max(finding.column, 1),
                    },
                }
            }
        ],
    }


def _budget_result(reason: str, config_path: str | None) -> dict[str, object]:
    rule = RULES["ASG100"]
    result: dict[str, object] = {
        "ruleId": "ASG100",
        "level": "error",
        "message": {"text": f"{reason} {rule.fix}"},
    }
    if config_path:
        result["locations"] = [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": config_path.replace("\\", "/")},
                    "region": {"startLine": 1, "startColumn": 1},
                }
            }
        ]
    return result


def _sarif_level(severity: str) -> str:
    if severity in {"error", "warning", "note"}:
        return severity
    return "warning"


def _usd(value: Decimal | None) -> str:
    if value is None:
        return ""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
