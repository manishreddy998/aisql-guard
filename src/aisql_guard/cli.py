"""Command line for scan, estimate, and check."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from aisql_guard.config import ConfigError, GuardConfig, load_config
from aisql_guard.engine import lint_paths
from aisql_guard.estimate import EstimateResult, estimate_calls
from aisql_guard.models import Finding
from aisql_guard.reporters import Report, render


def main(argv: list[str] | None = None) -> int:
    """Run a command and return a process status."""

    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else int(code)
    try:
        return _execute(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aisql-guard",
        description="Catch surprise costs from AI functions in SQL before the query runs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    commands = (
        ("scan", "Lint SQL files and dbt models."),
        ("estimate", "Estimate AI cost and apply budgets."),
        ("check", "Lint, estimate, and apply budgets."),
    )
    for name, help_text in commands:
        command = sub.add_parser(name, help=help_text)
        command.add_argument("paths", nargs="*", default=["."], help="SQL files or directories.")
        command.add_argument(
            "--format",
            choices=("text", "json", "sarif"),
            default="text",
            help="Report format.",
        )
        command.add_argument("--output", help="Write the report to this file instead of stdout.")
        command.add_argument(
            "--config",
            help="Config file. The default search is aisql-guard.toml, then pyproject.toml.",
        )
        command.add_argument("--dialect", help="sqlglot dialect. Overrides the config file.")
        command.add_argument(
            "--fail-on",
            choices=("note", "warning", "error"),
            help="Lowest severity that fails the run.",
        )
        command.add_argument(
            "--live",
            action="store_true",
            help="Sample input tokens from BigQuery. Off unless you pass this flag.",
        )
        command.add_argument("--no-color", action="store_true", help="Plain text without color.")
    return parser


def _execute(args: argparse.Namespace) -> int:
    paths = [Path(item) for item in args.paths]
    for path in paths:
        if not path.exists():
            print(f"error: path not found: {path}", file=sys.stderr)
            return 2
    explicit = Path(args.config) if args.config else None
    start = paths[0] if paths else Path.cwd()
    config = load_config(start, explicit=explicit)
    if args.dialect:
        config = replace(config, dialect=args.dialect)
    if args.fail_on:
        config = replace(config, fail_on=args.fail_on)

    show_findings = args.command in {"scan", "check"}
    show_estimate = args.command in {"estimate", "check"}
    try:
        findings, estimate = _collect(paths, config, show_estimate=show_estimate, live=args.live)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report = Report(
        findings=findings if show_findings else [],
        estimate=estimate if show_estimate else None,
        fail_on=config.fail_on,
        show_findings=show_findings,
        show_estimate=show_estimate,
        config_path=_config_uri(config.path),
    )
    color = sys.stdout.isatty() and not args.no_color and args.format == "text"
    text = render(report, args.format, color=color)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 1 if report.failed else 0


def _collect(
    paths: list[Path],
    config: GuardConfig,
    *,
    show_estimate: bool,
    live: bool,
) -> tuple[list[Finding], EstimateResult | None]:
    linted = lint_paths(paths, dialect=config.dialect, disabled=set(config.disabled_rules))
    estimate = None
    if show_estimate:
        if live:
            from aisql_guard.live import connect_bigquery

            probe = connect_bigquery(config.live_project, config.live_location)
            estimate = estimate_calls(linted.calls, config, live=True, probe=probe)
        else:
            estimate = estimate_calls(linted.calls, config, live=False)
    return linted.findings, estimate


def _config_uri(path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.name
