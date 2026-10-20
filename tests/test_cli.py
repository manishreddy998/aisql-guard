"""The CLI lints, estimates, and returns a status."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aisql_guard.cli import main

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "examples" / "support_tickets"


def test_check_blocks_on_the_example_project(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["check", str(PROJECT), "--format", "json", "--no-color"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["failed"] is True
    assert payload["budget_blocked"] is True
    assert any(item["rule_id"] == "ASG001" for item in payload["findings"])


def test_scan_of_a_capped_model_passes(capsys: pytest.CaptureFixture[str]) -> None:
    path = PROJECT / "models" / "marts" / "capped_batch.sql"
    code = main(["scan", str(path), "--no-color"])
    captured = capsys.readouterr()
    assert code == 0
    assert "no findings" in captured.out
    assert "result: ok" in captured.out


def test_estimate_text_shows_the_risky_total(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["estimate", str(PROJECT), "--no-color"])
    captured = capsys.readouterr()
    assert code == 1
    assert "1264.5" in captured.out
    assert "result: blocked" in captured.out
    assert "ASG001" not in captured.out


def test_sarif_written_to_a_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "report.sarif"
    code = main(["check", str(PROJECT), "--format", "sarif", "--output", str(output)])
    assert code == 1
    assert capsys.readouterr().out == ""
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["version"] == "2.1.0"
    assert document["runs"][0]["results"]


def test_warning_only_file_passes_until_fail_on_is_lowered(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = PROJECT / "models" / "marts" / "star_prompt.sql"
    assert main(["scan", str(path)]) == 0
    capsys.readouterr()
    assert main(["scan", str(path), "--fail-on", "warning"]) == 1


def test_missing_path_and_config_are_usage_errors(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", "missing.sql"]) == 2
    captured = capsys.readouterr()
    assert "not found" in captured.err
    assert main(["scan", str(PROJECT), "--config", "missing.toml"]) == 2
    assert "not found" in capsys.readouterr().err


def test_live_stays_off_until_the_flag_is_passed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("live connect should not run")

    monkeypatch.setattr("aisql_guard.live.connect_bigquery", boom)
    path = PROJECT / "models" / "marts" / "capped_batch.sql"
    assert main(["estimate", str(path), "--no-color"]) == 0
    code = main(["estimate", str(path), "--live"])
    assert code == 2
    assert "live connect should not run" in capsys.readouterr().err


def test_unknown_command_exits_with_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["lint"]) == 2
    assert capsys.readouterr().err


def test_module_entrypoint_scans_a_clean_model() -> None:
    path = PROJECT / "models" / "marts" / "capped_batch.sql"
    completed = subprocess.run(
        [sys.executable, "-m", "aisql_guard", "scan", str(path), "--no-color"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert completed.returncode == 0
    assert "result: ok" in completed.stdout
