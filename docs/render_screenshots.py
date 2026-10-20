"""Rebuild the SARIF annotation picture from a real check of the example project.

The terminal pictures under docs/screenshots are photographs of a local run.
This script does not replace those.

Run from the repository root:

    python docs/render_screenshots.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import cairosvg

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "examples" / "support_tickets"
DOCS = ROOT / "docs"
FONT = "JetBrains Mono, DejaVu Sans Mono, monospace"


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    sarif_path = DOCS / "support-tickets.sarif"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisql_guard",
            "check",
            str(PROJECT),
            "--format",
            "sarif",
            "--output",
            str(sarif_path),
            "--no-color",
        ],
        cwd=ROOT,
        check=False,
    )
    if completed.returncode != 1:
        raise SystemExit(f"expected the example project to be blocked, got {completed.returncode}")

    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    annotation = annotation_svg(sarif)
    (DOCS / "sarif-annotation.svg").write_text(annotation, encoding="utf-8")
    cairosvg.svg2png(
        bytestring=annotation.encode("utf-8"),
        write_to=str(DOCS / "sarif-annotation.png"),
    )


def annotation_svg(sarif: dict[str, object]) -> str:
    """Draw two SARIF results the way a code scanning comment would read."""

    results = sarif["runs"][0]["results"]  # type: ignore[index]
    missing_cap = next(item for item in results if item["ruleId"] == "ASG001")
    budget = next(item for item in results if item["ruleId"] == "ASG100")
    sql_path = PROJECT / "models" / "marts" / "ticket_summaries_risky.sql"
    sql_lines = sql_path.read_text(encoding="utf-8").splitlines()
    config_lines = (PROJECT / "aisql-guard.toml").read_text(encoding="utf-8").splitlines()

    parts: list[str] = []
    y = 28

    def text(value: str, x: int, size: int, fill: str, weight: str = "400") -> None:
        parts.append(
            f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}">{escape(value)}</text>'
        )

    def advance(amount: int) -> None:
        nonlocal y
        y += amount

    text("SARIF annotation from aisql-guard check", 36, 18, "#24292f", "700")
    advance(28)
    text("examples/support_tickets, the sample dbt project", 36, 14, "#57606a")
    advance(28)

    y = card(
        parts,
        y,
        title="ASG001  error",
        path="models/marts/ticket_summaries_risky.sql",
        code_lines=sql_lines,
        highlight=2,
        message=str(missing_cap["message"]["text"]),
    )
    advance(20)
    y = card(
        parts,
        y,
        title="ASG100  error",
        path="aisql-guard.toml",
        code_lines=config_lines[26:31],
        highlight=5,
        first_number=27,
        message=str(budget["message"]["text"]),
    )
    height = y + 28
    body = "\n".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="960" height="{height}" '
        f'viewBox="0 0 960 {height}">\n'
        f'<rect width="960" height="{height}" fill="#f4f6f8"/>\n'
        f"{body}\n</svg>\n"
    )


def card(
    parts: list[str],
    top: int,
    *,
    title: str,
    path: str,
    code_lines: list[str],
    highlight: int,
    message: str,
    first_number: int = 1,
) -> int:
    """Draw one result card and return the y position just below it."""

    wrapped = wrap(message, 92)
    code_height = 16 + len(code_lines) * 22
    message_height = 16 + len(wrapped) * 22
    height = 44 + code_height + message_height + 16
    parts.append(
        f'<rect x="24" y="{top}" width="912" height="{height}" rx="8" '
        'fill="#ffffff" stroke="#d0d7de"/>'
    )
    parts.append(f'<rect x="24" y="{top}" width="6" height="{height}" rx="3" fill="#cf222e"/>')
    title_y = top + 28
    parts.append(
        f'<text x="44" y="{title_y}" font-family="{FONT}" font-size="15" '
        f'font-weight="700" fill="#cf222e">{escape(title)}</text>'
    )
    parts.append(
        f'<text x="220" y="{title_y}" font-family="{FONT}" font-size="14" '
        f'fill="#57606a">{escape(path)}</text>'
    )
    code_top = top + 44
    parts.append(
        f'<rect x="40" y="{code_top}" width="880" height="{code_height}" rx="6" fill="#f6f8fa"/>'
    )
    for index, line in enumerate(code_lines):
        number = first_number + index
        line_y = code_top + 22 + index * 22
        if number == highlight or (first_number != 1 and index + 1 == highlight):
            parts.append(
                f'<rect x="40" y="{line_y - 16}" width="880" height="22" fill="#fff8c5"/>'
            )
        parts.append(
            f'<text x="52" y="{line_y}" font-family="{FONT}" font-size="13" '
            f'fill="#8c959f">{number}</text>'
        )
        parts.append(
            f'<text x="92" y="{line_y}" font-family="{FONT}" font-size="13" '
            f'fill="#24292f">{escape(line)}</text>'
        )
    message_y = code_top + code_height + 22
    for index, line in enumerate(wrapped):
        parts.append(
            f'<text x="44" y="{message_y + index * 22}" font-family="{FONT}" '
            f'font-size="14" fill="#24292f">{escape(line)}</text>'
        )
    return top + height


def wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if len(trial) > width and current:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


if __name__ == "__main__":
    main()
