"""Turn dbt Jinja into SQL that sqlglot can parse, without moving lines."""

from __future__ import annotations

import re

_TOKEN = re.compile(
    r"\{\{(?P<expr>.*?)\}\}|\{%(?P<tag>.*?)%\}|\{#(?P<comment>.*?)#\}",
    re.DOTALL,
)
_CONFIG_CALL = re.compile(r"(?is)\{\{\s*config\s*\((.*?)\)\s*\}\}")
_MATERIALIZED = re.compile(r"(?is)materialized\s*=\s*['\"]([A-Za-z0-9_]+)['\"]")
_REF = re.compile(r"(?is)\bref\s*\(\s*['\"]([A-Za-z0-9_.]+)['\"]")
_SOURCE = re.compile(
    r"(?is)\bsource\s*\(\s*['\"]([A-Za-z0-9_]+)['\"]\s*,\s*['\"]([A-Za-z0-9_]+)['\"]"
)
_THIS = re.compile(r"(?i)\bthis\b")
_INCREMENTAL = re.compile(r"(?i)\bis_incremental\s*\(")
_NOT_INCREMENTAL = re.compile(r"(?i)\bnot\s+is_incremental\b")


def extract_materialized(sql: str) -> str | None:
    """Return the materialized value from a model config block, if one is set."""

    found: str | None = None
    for match in _CONFIG_CALL.finditer(sql):
        materialized = _MATERIALIZED.search(match.group(1))
        if materialized is not None:
            found = materialized.group(1).lower()
    return found


def render_for_parse(sql: str, *, incremental: bool) -> str:
    """Replace Jinja with SQL placeholders.

    Line breaks stay put, so a later finding still points at the model.
    ``incremental`` selects the branch of ``is_incremental()``. Other if
    blocks keep the first branch. That covers a normal dbt model. Macros
    that assemble SQL inside ``{% set %}`` blocks are left as text and may
    fail to parse.
    """

    chars = list(sql)
    frames: list[_Frame] = []
    for match in _TOKEN.finditer(sql):
        if match.group("comment") is not None:
            _blank(chars, match.start(), match.end())
            continue
        if match.group("expr") is not None:
            if _in_dropped_branch(frames):
                _blank(chars, match.start(), match.end())
            else:
                _replace_expr(chars, match.start(), match.end(), match.group("expr") or "")
            continue
        _blank(chars, match.start(), match.end())
        _apply_tag(frames, match, chars, incremental=incremental)
    return "".join(chars)


class _Frame:
    def __init__(self, keep_if: bool, content_start: int) -> None:
        self.keep_if = keep_if
        self.branch = "if"
        self.body_start = content_start


def _apply_tag(
    frames: list[_Frame],
    match: re.Match[str],
    chars: list[str],
    *,
    incremental: bool,
) -> None:
    inner = (match.group("tag") or "").strip().lstrip("-").rstrip("-").strip()
    lower = inner.lower()
    if lower.startswith("if "):
        frames.append(_Frame(_keep_if_branch(inner[3:].strip(), incremental), match.end()))
        return
    if lower == "else" or lower.startswith("else ") or lower.startswith("elif "):
        if not frames:
            return
        frame = frames[-1]
        _close_branch(frame, match.start(), chars)
        frame.branch = "else"
        frame.body_start = match.end()
        return
    if lower == "endif" or lower.startswith("endif"):
        if not frames:
            return
        frame = frames.pop()
        _close_branch(frame, match.start(), chars)


def _keep_if_branch(condition: str, incremental: bool) -> bool:
    if not _INCREMENTAL.search(condition):
        return True
    negated = _NOT_INCREMENTAL.search(condition) is not None
    if negated:
        return not incremental
    return incremental


def _close_branch(frame: _Frame, body_end: int, chars: list[str]) -> None:
    keeping = (frame.branch == "if" and frame.keep_if) or (
        frame.branch == "else" and not frame.keep_if
    )
    if not keeping:
        _blank(chars, frame.body_start, body_end)


def _in_dropped_branch(frames: list[_Frame]) -> bool:
    for frame in frames:
        if frame.branch == "if" and not frame.keep_if:
            return True
        if frame.branch == "else" and frame.keep_if:
            return True
    return False


def _blank(chars: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if chars[index] != "\n":
            chars[index] = " "


def _replace_expr(chars: list[str], start: int, end: int, expr: str) -> None:
    replacement = _expr_sql(expr)
    width = end - start
    if len(replacement) > width:
        replacement = replacement[:width]
    padded = replacement + (" " * (width - len(replacement)))
    for offset, char in enumerate(padded):
        chars[start + offset] = char


def _expr_sql(expr: str) -> str:
    if re.search(r"(?i)\bconfig\s*\(", expr):
        return ""
    source = _SOURCE.search(expr)
    if source is not None:
        return _ident(f"{source.group(1)}_{source.group(2)}")
    ref = _REF.search(expr)
    if ref is not None:
        return _ident(ref.group(1))
    if _THIS.search(expr):
        return "dbt_this"
    return "NULL"


def _ident(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not cleaned:
        return "model"
    if cleaned[0].isdigit():
        return f"m_{cleaned}"
    return cleaned
