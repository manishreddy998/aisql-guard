"""Catch surprise costs from AI functions in SQL before the query runs."""

from aisql_guard.engine import lint_paths, lint_sql

__all__ = ["lint_paths", "lint_sql", "__version__"]
__version__ = "0.1.0"
