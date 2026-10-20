"""Find AI function calls and the SQL shapes around them."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from aisql_guard.catalog import SCAN_NAMES, FunctionSpec, get_spec

_OUTPUT_CAP = re.compile(r"(?i)max_output_tokens|maxoutputtokens|\bmax_tokens\b|\bmaxtokens\b")
_THINKING = re.compile(r"(?i)thinking_budget|thinkingbudget|thinking_level|thinkinglevel")
_CAP_NUMBERS = (
    re.compile(r"(?i)max_output_tokens['\"]?\s*[:=]\s*(\d+)"),
    re.compile(r"(?i)maxoutputtokens['\"]?\s*[:=]\s*(\d+)"),
    re.compile(r"(?i)(\d+)\s+as\s+max_output_tokens\b"),
    re.compile(r"(?i)max_output_tokens\s*=>\s*(\d+)"),
    re.compile(r"(?i)max_tokens['\"]?\s*[:=]\s*(\d+)"),
    re.compile(r"(?i)['\"]max_tokens['\"]\s*,\s*(\d+)"),
    re.compile(r"(?i)(\d+)\s+as\s+max_tokens\b"),
    re.compile(r"(?i)max_tokens\s*=>\s*(\d+)"),
)
_THINK_NUMBERS = (
    re.compile(r"(?i)thinking_budget['\"]?\s*[:=]\s*(\d+)"),
    re.compile(r"(?i)thinkingbudget['\"]?\s*[:=]\s*(\d+)"),
    re.compile(r"(?i)(\d+)\s+as\s+thinking_budget\b"),
    re.compile(r"(?i)thinking_budget\s*=>\s*(\d+)"),
)
_ENDPOINT = re.compile(r"(?i)endpoint\s*=>\s*'([^']+)'")
_FIRST_STRING = re.compile(r"(?i)\(\s*'([^']+)'")
_BQ_MODEL = re.compile(r"(?i)\bMODEL\s+`([^`]+)`")
_STAR = re.compile(r"(?is)\bselect\b(?:\s+as\s+struct)?\s+(?:distinct\s+)?(?:\w+\s*\.\s*)?\*")
_LIMIT_WORD = re.compile(r"(?i)\blimit\b|\boffset\b")

# sqlglot treats a few of these names as its own functions and then drops
# arguments. The stand-ins are the same length, so columns do not move.
_REWRITES = (
    (re.compile(r"(?i)\bAI\.IF\b"), "AIIF1"),
    (re.compile(r"(?i)\bAI\.AGG\b"), "AIAGG1"),
    (re.compile(r"(?i)\bAI_CLASSIFY\b"), "AICLASSIFY1"),
    (re.compile(r"(?i)\bai_classify\b"), "AICLASSIFY1"),
    (re.compile(r"(?i)\bAI_AGG\b"), "AIAGG2"),
)
_SENTINELS = {
    "AIIF1": "AI.IF",
    "AIAGG1": "AI.AGG",
    "AICLASSIFY1": "AI_CLASSIFY",
    "AIAGG2": "AI_AGG",
}
_TVF_TYPES = (
    exp.GenerateText,
    exp.GenerateTable,
    exp.GenerateBool,
    exp.GenerateInt,
    exp.GenerateDouble,
)
_THIS = "dbt_this"


@dataclass(frozen=True)
class CallSite:
    """A function call located in the original model text."""

    name: str
    spec: FunctionSpec
    start: int
    end: int
    text: str


@dataclass
class CallFact:
    """Structure around one call, used by the rule checks."""

    site: CallSite
    has_star: bool = False
    prompt_limit_at: int | None = None
    outer_limit_at: int | None = None
    upstream_limit_at: int | None = None
    unfiltered: bool = False


@dataclass
class FileFacts:
    """Everything the rules need from one rendered SQL string."""

    calls: list[CallFact] = field(default_factory=list)
    parse_failed: bool = False
    is_view_sql: bool = False
    has_skip_filter: bool = False


def analyze(sql: str, *, dialect: str) -> FileFacts:
    """Parse SQL and describe each AI function call."""

    sites = _scan_calls(sql)
    facts = FileFacts()
    try:
        statements = _parse(_rewrite_names(sql), dialect)
    except SqlglotError:
        facts.parse_failed = True
        for site in sites:
            facts.calls.append(_fact_from_text(sql, site))
        facts.has_skip_filter = _skip_filter_in_text(sql)
        return facts
    facts.is_view_sql = _is_view_sql(statements)
    facts.has_skip_filter = _has_skip_filter(statements)
    nodes = _catalog_nodes(statements)
    paired = _pair(sites, nodes)
    limits = _keyword_offsets(sql, _LIMIT_WORD)
    spans = _statement_spans(sql)
    for site, node in paired:
        facts.calls.append(_fact_from_node(sql, site, node, limits, spans))
    return facts


def position(sql: str, offset: int) -> tuple[int, int]:
    """Return a 1-based line and column for a character offset."""

    if offset < 0:
        offset = 0
    if offset > len(sql):
        offset = len(sql)
    line = sql.count("\n", 0, offset) + 1
    previous = sql.rfind("\n", 0, offset)
    return line, offset - previous


def has_output_cap(text: str) -> bool:
    """Return whether the call text sets an output token cap."""

    return _OUTPUT_CAP.search(text) is not None


def has_thinking_budget(text: str) -> bool:
    """Return whether the call text sets a thinking budget or level."""

    return _THINKING.search(text) is not None


def output_token_cap(text: str) -> int | None:
    """Return a numeric output cap written on the call, when one is obvious."""

    return _first_int(_CAP_NUMBERS, text)


def thinking_token_cap(text: str) -> int | None:
    """Return a numeric thinking budget written on the call."""

    return _first_int(_THINK_NUMBERS, text)


def model_hint(name: str, text: str) -> str | None:
    """Return the model or endpoint literal on the call, if it has one."""

    endpoint = _ENDPOINT.search(text)
    if endpoint is not None:
        return endpoint.group(1)
    if name in {"AI_COMPLETE", "SNOWFLAKE.CORTEX.COMPLETE", "AI_QUERY"}:
        first = _FIRST_STRING.search(text)
        if first is not None:
            return first.group(1)
    remote = _BQ_MODEL.search(text)
    if remote is not None:
        return remote.group(1)
    return None


def _fact_from_text(sql: str, site: CallSite) -> CallFact:
    limit_at = _first_keyword(site.text, _LIMIT_WORD)
    return CallFact(
        site=site,
        has_star=_STAR.search(site.text) is not None,
        prompt_limit_at=None if limit_at is None else site.start + limit_at,
        unfiltered=False,
    )


def _fact_from_node(
    sql: str,
    site: CallSite,
    node: exp.Expression | None,
    limits: list[int],
    spans: list[tuple[int, int]],
) -> CallFact:
    if node is None:
        return _fact_from_text(sql, site)
    fact = CallFact(site=site)
    fact.has_star = any(True for _ in node.find_all(exp.Star))
    fact.unfiltered = _call_unfiltered(node, _cte_map_for(node))
    inner_limits = [offset for offset in limits if site.start <= offset <= site.end]
    if _node_has_inner_limit(node) and inner_limits:
        fact.prompt_limit_at = inner_limits[0]
    elif _node_has_inner_limit(node):
        fact.prompt_limit_at = site.start
    statement = _span_for(spans, site.start)
    outside = [
        offset
        for offset in limits
        if statement[0] <= offset < statement[1] and not (site.start <= offset <= site.end)
    ]
    if _node_has_outer_limit(node):
        after = [offset for offset in outside if offset > site.end]
        fact.outer_limit_at = after[-1] if after else (outside[-1] if outside else site.start)
    if _node_has_upstream_limit(node):
        before = [offset for offset in outside if offset < site.start]
        chosen = before[-1] if before else None
        if chosen is not None and chosen != fact.outer_limit_at:
            fact.upstream_limit_at = chosen
        elif chosen is None and not _node_has_outer_limit(node) and outside:
            fact.upstream_limit_at = outside[0]
    return fact


def _pair(
    sites: list[CallSite], nodes: list[exp.Expression]
) -> list[tuple[CallSite, exp.Expression | None]]:
    if len(sites) == len(nodes):
        return list(zip(sites, nodes, strict=True))
    return [(site, None) for site in sites]


def _scan_calls(sql: str) -> list[CallSite]:
    calls: list[CallSite] = []
    index = 0
    length = len(sql)
    while index < length:
        index = _skip_trivia(sql, index)
        if index >= length:
            break
        if not _at_boundary(sql, index):
            index += 1
            continue
        matched = _match_name(sql, index)
        if matched is None:
            index += 1
            continue
        name, name_end = matched
        open_paren = _next_paren(sql, name_end)
        if open_paren is None:
            index = name_end
            continue
        close_paren = _matching_paren(sql, open_paren)
        spec = get_spec(name)
        if spec is None:
            index = name_end
            continue
        calls.append(
            CallSite(
                name=spec.name,
                spec=spec,
                start=index,
                end=close_paren,
                text=sql[index : close_paren + 1],
            )
        )
        index = close_paren + 1
    return calls


def _match_name(sql: str, index: int) -> tuple[str, int] | None:
    tail = sql[index:]
    for name in SCAN_NAMES:
        if len(tail) < len(name):
            continue
        if tail[: len(name)].upper() != name:
            continue
        end = index + len(name)
        if end < len(sql) and _is_ident(sql[end]):
            continue
        return name, end
    return None


def _next_paren(sql: str, index: int) -> int | None:
    length = len(sql)
    while index < length:
        index = _skip_trivia(sql, index)
        if index >= length:
            return None
        if sql[index] == "(":
            return index
        if sql[index].isspace():
            index += 1
            continue
        return None
    return None


def _matching_paren(sql: str, open_index: int) -> int:
    depth = 0
    index = open_index
    length = len(sql)
    while index < length:
        index = _skip_trivia(sql, index)
        if index >= length:
            break
        char = sql[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return length - 1


def _skip_trivia(sql: str, index: int) -> int:
    length = len(sql)
    while index < length:
        char = sql[index]
        if char == "-" and index + 1 < length and sql[index + 1] == "-":
            newline = sql.find("\n", index)
            return length if newline < 0 else newline + 1
        if char == "/" and index + 1 < length and sql[index + 1] == "*":
            end = sql.find("*/", index + 2)
            return length if end < 0 else end + 2
        if char in {"'", '"', "`"} or sql.startswith("'''", index) or sql.startswith('"""', index):
            return _skip_quote(sql, index)
        return index
    return index


def _skip_quote(sql: str, index: int) -> int:
    if sql.startswith("'''", index) or sql.startswith('"""', index):
        quote = sql[index : index + 3]
        end = sql.find(quote, index + 3)
        return len(sql) if end < 0 else end + 3
    quote = sql[index]
    index += 1
    length = len(sql)
    while index < length:
        if sql[index] == "\\" and quote != "`":
            index += 2
            continue
        if sql[index] == quote:
            if index + 1 < length and sql[index + 1] == quote:
                index += 2
                continue
            return index + 1
        index += 1
    return length


def _at_boundary(sql: str, index: int) -> bool:
    if index == 0:
        return True
    previous = sql[index - 1]
    return not (_is_ident(previous) or previous == ".")


def _is_ident(char: str) -> bool:
    return char.isalnum() or char == "_"


def _rewrite_names(sql: str) -> str:
    rewritten = sql
    for pattern, replacement in _REWRITES:
        rewritten = pattern.sub(replacement, rewritten)
    return rewritten


def _parse(sql: str, dialect: str) -> list[exp.Expression]:
    dialects = [dialect]
    for extra in ("bigquery", "snowflake", "databricks", "spark"):
        if extra not in dialects:
            dialects.append(extra)
    last_error: SqlglotError | None = None
    for item in dialects:
        try:
            parsed = sqlglot.parse(sql, read=item, error_level=sqlglot.ErrorLevel.RAISE)
        except SqlglotError as exc:
            last_error = exc
            continue
        statements: list[exp.Expression] = []
        for statement in parsed:
            if statement is not None:
                statements.append(cast(exp.Expression, statement))
        return statements
    if last_error is None:
        return []
    raise last_error


def _catalog_nodes(statements: list[exp.Expression]) -> list[exp.Expression]:
    nodes: list[exp.Expression] = []
    for statement in statements:
        for node in statement.walk():
            expression = cast(exp.Expression, node)
            if _node_label(expression) is not None:
                nodes.append(expression)
    return nodes


def _node_label(node: exp.Expression) -> str | None:
    if isinstance(node, exp.AIGenerate):
        label = "AI.GENERATE"
    elif isinstance(node, _TVF_TYPES):
        base = {
            exp.GenerateText: "GENERATE_TEXT",
            exp.GenerateTable: "GENERATE_TABLE",
            exp.GenerateBool: "GENERATE_BOOL",
            exp.GenerateInt: "GENERATE_INT",
            exp.GenerateDouble: "GENERATE_DOUBLE",
        }[type(node)]
        qualifier = _qualifier(node)
        label = f"{qualifier}.{base}" if qualifier else base
    elif isinstance(node, exp.Anonymous):
        raw = str(node.this).upper()
        if raw in _SENTINELS:
            label = _SENTINELS[raw]
        else:
            qualifier = _qualifier(node)
            label = f"{qualifier}.{raw}" if qualifier else raw
    else:
        return None
    if get_spec(label) is None:
        return None
    return label


def _qualifier(node: exp.Expression) -> str:
    parent = node.parent
    if isinstance(parent, exp.Table) and parent.this is node:
        parts: list[str] = []
        catalog = parent.args.get("catalog")
        db = parent.args.get("db")
        if isinstance(catalog, exp.Identifier):
            parts.append(catalog.name)
        if isinstance(db, exp.Identifier):
            parts.append(db.name)
        return ".".join(parts).upper()
    if isinstance(parent, exp.Dot) and parent.expression is node:
        return ".".join(_dot_idents(parent.this)).upper()
    return ""


def _dot_idents(node: exp.Expression) -> list[str]:
    if isinstance(node, exp.Identifier):
        return [node.name]
    if isinstance(node, exp.Column):
        parts: list[str] = []
        if node.table:
            parts.append(node.table)
        parts.append(node.name)
        return parts
    if isinstance(node, exp.Dot):
        return _dot_idents(node.this) + _dot_idents(node.expression)
    return []


def _is_view_sql(statements: list[exp.Expression]) -> bool:
    for statement in statements:
        kind = str(statement.args.get("kind") or "").upper()
        if isinstance(statement, exp.Create) and kind == "VIEW":
            return True
    return False


def _has_skip_filter(statements: list[exp.Expression]) -> bool:
    return any(_statement_skips(statement) for statement in statements)


def _statement_skips(statement: exp.Expression) -> bool:
    for node in statement.find_all(exp.Not):
        inner = node.this
        if isinstance(inner, (exp.Exists, exp.In)) and _mentions_this(inner):
            return True
    for join in statement.find_all(exp.Join):
        if not isinstance(join.this, exp.Table) or _THIS not in _aliases(join.this):
            continue
        kind = str(join.args.get("kind") or "").upper()
        side = str(join.args.get("side") or "").upper()
        if kind == "ANTI":
            return True
        if side == "LEFT" and _null_rejects(join):
            return True
    for compare_type in (exp.GT, exp.GTE, exp.LT, exp.LTE):
        for compare in statement.find_all(compare_type):
            if _is_watermark(cast(exp.Expression, compare)):
                return True
    return any(_mentions_this(node) for node in statement.find_all(exp.Except))


def _mentions_this(node: exp.Expression) -> bool:
    for table in node.find_all(exp.Table):
        if (table.name or "").lower() == _THIS:
            return True
    return False


def _aliases(table: exp.Table) -> set[str]:
    names = {(table.name or "").lower()}
    alias = table.args.get("alias")
    if alias is not None and getattr(alias, "name", None):
        names.add(str(alias.name).lower())
    return {name for name in names if name}


def _null_rejects(join: exp.Join) -> bool:
    if not isinstance(join.this, exp.Table):
        return False
    aliases = _aliases(join.this)
    select = _enclosing_select(join)
    if select is None:
        return False
    where = select.args.get("where")
    if where is None:
        return False
    for is_node in where.find_all(exp.Is):
        if not isinstance(is_node.expression, exp.Null):
            continue
        column = is_node.this
        if isinstance(column, exp.Column) and (column.table or "").lower() in aliases:
            return True
    return False


def _is_watermark(compare: exp.Expression) -> bool:
    for side_name in ("this", "expression"):
        side = _unwrap(compare.args.get(side_name))
        if isinstance(side, exp.Subquery) and _mentions_this(side):
            return True
    return False


def _unwrap(node: exp.Expression | None) -> exp.Expression | None:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _enclosing_select(node: exp.Expression) -> exp.Select | None:
    current = node.parent
    while current is not None:
        if isinstance(current, exp.Select):
            return current
        current = current.parent
    return None


def _cte_map_for(node: exp.Expression) -> dict[str, exp.Select]:
    root: exp.Expression = node
    while root.parent is not None:
        root = cast(exp.Expression, root.parent)
    found: dict[str, exp.Select] = {}
    for cte in root.find_all(exp.CTE):
        alias = cte.args.get("alias")
        name = str(getattr(alias, "name", "") or "").lower()
        body = cte.this
        if isinstance(body, exp.Subquery):
            body = body.this
        if name and isinstance(body, exp.Select):
            found[name] = body
    return found


def _call_unfiltered(node: exp.Expression, ctes: dict[str, exp.Select]) -> bool:
    if isinstance(node, _TVF_TYPES):
        expression = node.args.get("expression")
        if isinstance(expression, exp.Table):
            return True
        if isinstance(expression, exp.Subquery) and isinstance(expression.this, exp.Select):
            return _select_unfiltered(expression.this, ctes, set())
        return False
    select = _enclosing_select(node)
    if select is None:
        return False
    return _select_unfiltered(select, ctes, set())


def _select_unfiltered(
    select: exp.Select,
    ctes: dict[str, exp.Select],
    seen: set[str],
) -> bool:
    if select.args.get("where") is not None:
        return False
    sources = _sources(select)
    if not sources:
        return False
    return any(_source_unfiltered(source, ctes, seen) for source in sources)


def _source_unfiltered(
    source: exp.Expression,
    ctes: dict[str, exp.Select],
    seen: set[str],
) -> bool:
    if isinstance(source, exp.Subquery) and isinstance(source.this, exp.Select):
        return _select_unfiltered(source.this, ctes, seen)
    if isinstance(source, exp.Table):
        if not isinstance(source.this, exp.Identifier):
            return False
        name = (source.name or "").lower()
        if name in ctes:
            if name in seen:
                return True
            seen.add(name)
            return _select_unfiltered(ctes[name], ctes, seen)
        return True
    return False


def _sources(select: exp.Select) -> list[exp.Expression]:
    items: list[exp.Expression] = []
    from_ = select.args.get("from_")
    if from_ is not None and getattr(from_, "this", None) is not None:
        items.append(from_.this)
    for join in select.args.get("joins") or []:
        if getattr(join, "this", None) is not None:
            items.append(join.this)
    return items


def _node_has_inner_limit(node: exp.Expression) -> bool:
    return any(_select_limited(select) for select in node.find_all(exp.Select))


def _node_has_outer_limit(node: exp.Expression) -> bool:
    select = _enclosing_select(node)
    return select is not None and _select_limited(select)


def _node_has_upstream_limit(node: exp.Expression) -> bool:
    if isinstance(node, _TVF_TYPES):
        expression = node.args.get("expression")
        if isinstance(expression, exp.Subquery) and isinstance(expression.this, exp.Select):
            return _upstream_limited(expression.this, _cte_map_for(node), set())
        return False
    select = _enclosing_select(node)
    if select is None:
        return False
    return _upstream_limited(select, _cte_map_for(node), set())


def _upstream_limited(
    select: exp.Select,
    ctes: dict[str, exp.Select],
    seen: set[str],
) -> bool:
    for source in _sources(select):
        if isinstance(source, exp.Subquery) and isinstance(source.this, exp.Select):
            inner = source.this
            if _select_limited(inner) or _upstream_limited(inner, ctes, seen):
                return True
        if isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier):
            name = (source.name or "").lower()
            if name in ctes and name not in seen:
                seen.add(name)
                cte_select = ctes[name]
                if _select_limited(cte_select) or _upstream_limited(cte_select, ctes, seen):
                    return True
    return False


def _select_limited(select: exp.Select) -> bool:
    return select.args.get("limit") is not None or select.args.get("offset") is not None


def _keyword_offsets(sql: str, pattern: re.Pattern[str]) -> list[int]:
    offsets: list[int] = []
    index = 0
    length = len(sql)
    while index < length:
        skipped = _skip_trivia(sql, index)
        if skipped != index:
            index = skipped
            continue
        if _is_ident(sql[index]) or sql[index] == ".":
            match = pattern.match(sql, index)
            if match and _at_boundary(sql, index):
                after = match.end()
                if after >= length or not _is_ident(sql[after]):
                    offsets.append(index)
                    index = after
                    continue
        index += 1
    return offsets


def _first_keyword(text: str, pattern: re.Pattern[str]) -> int | None:
    offsets = _keyword_offsets(text, pattern)
    if not offsets:
        return None
    return offsets[0]


def _statement_spans(sql: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    depth = 0
    index = 0
    length = len(sql)
    while index < length:
        skipped = _skip_trivia(sql, index)
        if skipped != index:
            index = skipped
            continue
        char = sql[index]
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        elif char == ";" and depth == 0:
            spans.append((start, index))
            start = index + 1
        index += 1
    spans.append((start, length))
    return spans


def _span_for(spans: list[tuple[int, int]], offset: int) -> tuple[int, int]:
    for span in spans:
        if span[0] <= offset <= span[1]:
            return span
    return (0, 0)


def _skip_filter_in_text(sql: str) -> bool:
    if _THIS not in sql.lower():
        return False
    patterns = (
        r"(?is)not\s+exists\s*\(.*dbt_this",
        r"(?is)not\s+in\s*\(.*dbt_this",
        r"(?is)left\s+join\s+dbt_this.*is\s+null",
        r"(?is)(?:>|>=|<|<=)\s*\(\s*select.*dbt_this",
    )
    return any(re.search(pattern, sql) for pattern in patterns)


def _first_int(patterns: tuple[re.Pattern[str], ...], text: str) -> int | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match is not None:
            return int(match.group(1))
    return None
