"""The README pictures come from a real check of the sample project."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def test_terminal_picture_shows_the_blocked_sample() -> None:
    svg = (DOCS / "check-terminal.svg").read_text(encoding="utf-8")
    assert "ticket_summaries_risky" in svg
    assert "1264.5" in svg
    assert "result" in svg and "blocked" in svg
    assert "ASG001" in svg
    assert "Generated with" not in svg
    png = (DOCS / "check-terminal.png").read_bytes()
    assert png.startswith(b"\x89PNG")


def test_sarif_annotation_uses_the_check_output() -> None:
    svg = (DOCS / "sarif-annotation.svg").read_text(encoding="utf-8")
    assert "ASG001" in svg
    assert "ASG100" in svg
    assert "1264.5" in svg
    assert "ticket_summaries_risky.sql" in svg
    png = (DOCS / "sarif-annotation.png").read_bytes()
    assert png.startswith(b"\x89PNG")

    document = json.loads((DOCS / "support-tickets.sarif").read_text(encoding="utf-8"))
    assert document["version"] == "2.1.0"
    rule_ids = {item["ruleId"] for item in document["runs"][0]["results"]}
    assert {"ASG001", "ASG004", "ASG005", "ASG100"} <= rule_ids


def test_readme_has_the_diagram_and_the_pictures() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "```mermaid" in readme
    assert "docs/check-terminal.png" in readme
    assert "docs/sarif-annotation.png" in readme
    assert "ASG003" in readme
    assert "ASG100" in readme
