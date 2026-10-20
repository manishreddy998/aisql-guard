"""Catch surprise costs from AI functions in SQL before the query runs."""

from aisql_guard.config import load_config
from aisql_guard.engine import lint_paths, lint_sql
from aisql_guard.estimate import estimate_paths

__all__ = ["estimate_paths", "lint_paths", "lint_sql", "load_config", "__version__"]
__version__ = "0.1.0"
