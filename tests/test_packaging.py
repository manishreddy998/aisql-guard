"""The action, the hook, and CI install this repo. They do not publish it."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_action_installs_the_checkout_and_stays_offline_by_default() -> None:
    text = (ROOT / "action.yml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["runs"]["using"] == "composite"
    assert "github.action_path" in text
    assert "pypi.org" not in text.lower()
    assert data["inputs"]["live"]["default"] == "false"
    assert data["inputs"]["command"]["default"] == "check"
    assert data["inputs"]["format"]["default"] == "sarif"


def test_pre_commit_hook_checks_sql() -> None:
    hooks = yaml.safe_load((ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8"))
    assert hooks[0]["id"] == "aisql-guard"
    assert hooks[0]["entry"] == "aisql-guard check"
    assert hooks[0]["language"] == "python"
    assert "sql" in hooks[0]["types_or"]
    assert hooks[0]["pass_filenames"] is True


def test_ci_runs_lint_types_and_tests_on_supported_pythons() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["test"]
    assert job["strategy"]["matrix"]["python-version"] == ["3.10", "3.11", "3.12", "3.13"]
    steps = [step.get("run", "") for step in job["steps"]]
    joined = "\n".join(steps)
    assert "ruff check" in joined
    assert "mypy" in joined
    assert "pytest" in joined
    assert "twine" not in joined
    assert "pypi" not in joined.lower()


def test_code_scanning_workflow_runs_the_action_and_uploads_sarif() -> None:
    path = ROOT / ".github" / "workflows" / "code-scanning.yml"
    text = path.read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert "uses: ./" in text
    assert "security-events: write" in text
    data = yaml.safe_load(text)
    steps = data["jobs"]["aisql-guard"]["steps"]
    action = next(step for step in steps if step.get("uses") == "./")
    assert action["with"]["paths"] == "."
    assert action["with"]["format"] == "sarif"
    upload = next(
        step for step in steps if step.get("uses") == "github/codeql-action/upload-sarif@v3"
    )
    assert upload["if"] == "always()"
    assert upload["with"]["sarif_file"] == "aisql-guard.sarif"


def test_clean_install_job_uses_a_fresh_pip_install() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["install"]
    assert job["strategy"]["matrix"]["pip"] == ["latest", "24.0"]
    steps = "\n".join(step.get("run", "") for step in job["steps"])
    assert "python -m pip install ." in steps
    assert "aisql-guard check examples/support_tickets" in steps
    assert "pytest" in steps
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'license = "MIT"' in project
    assert "License :: OSI Approved :: MIT License" not in project
    assert "setuptools>=77" in project
