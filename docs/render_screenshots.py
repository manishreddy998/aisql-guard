"""Refresh docs/support-tickets.sarif from a real check of the example project.

The pictures under docs/screenshots are photographs. This script does not
replace them.

Run from the repository root:

    python docs/render_screenshots.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "examples" / "support_tickets"
DOCS = ROOT / "docs"


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


if __name__ == "__main__":
    main()
