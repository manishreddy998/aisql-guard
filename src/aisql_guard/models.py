"""Shared records for lint findings and AI calls."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Finding:
    """One lint result, located in a source file."""

    rule_id: str
    severity: str
    message: str
    fix: str
    path: str
    line: int
    column: int
    model_name: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "fix": self.fix,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "model_name": self.model_name,
        }


@dataclass(frozen=True)
class AiCall:
    """An AI function call found in a SQL file."""

    name: str
    provider: str
    path: str
    line: int
    column: int
    model_name: str | None
    model_hint: str | None
    has_output_cap: bool
    output_token_cap: int | None
    has_thinking_budget: bool
    thinking_token_cap: int | None
    needs_output_cap: bool
    needs_thinking_budget: bool


@dataclass
class LintResult:
    """Findings and the AI calls they came from."""

    findings: list[Finding] = field(default_factory=list)
    calls: list[AiCall] = field(default_factory=list)
