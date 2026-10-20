"""The README pictures come from a real check of the sample project."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def test_real_screenshots_are_pngs() -> None:
    names = (
        "screenshots/check-findings.png",
        "screenshots/check-estimate.png",
        "screenshots/tests-passing.png",
    )
    for name in names:
        png = (DOCS / name).read_bytes()
        assert png.startswith(b"\x89PNG")
        assert len(png) > 1000


def test_code_scanning_screenshots_are_pngs() -> None:
    names = (
        "screenshots/scan-asg001.png",
        "screenshots/scan-asg002.png",
        "screenshots/scan-asg006.png",
        "screenshots/scan-asg007.png",
    )
    for name in names:
        png = (DOCS / name).read_bytes()
        assert png.startswith(b"\x89PNG")
        assert len(png) > 1000
    assert not (DOCS / "sarif-annotation.png").exists()
    assert not (DOCS / "sarif-annotation.svg").exists()

    document = json.loads((DOCS / "support-tickets.sarif").read_text(encoding="utf-8"))
    assert document["version"] == "2.1.0"
    rule_ids = {item["ruleId"] for item in document["runs"][0]["results"]}
    assert {"ASG001", "ASG004", "ASG005", "ASG100"} <= rule_ids


def test_readme_has_the_diagram_and_the_pictures() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "```mermaid" in readme
    assert "docs/screenshots/check-findings.png" in readme
    assert "docs/screenshots/check-estimate.png" in readme
    assert "docs/screenshots/tests-passing.png" in readme
    assert "docs/screenshots/scan-asg001.png" in readme
    assert "docs/screenshots/scan-asg002.png" in readme
    assert "docs/screenshots/scan-asg006.png" in readme
    assert "docs/screenshots/scan-asg007.png" in readme
    assert "https://github.com/manishreddy998/aisql-guard/pull/10" in readme
    assert "docs/support-tickets.sarif" in readme
    assert "docs/sarif-annotation.png" not in readme
    assert "docs/sarif-annotation.svg" not in readme
    assert "docs/check-terminal.png" not in readme
    assert "docs/check-terminal.svg" not in readme
    assert "ASG003" in readme
    assert "ASG100" in readme
