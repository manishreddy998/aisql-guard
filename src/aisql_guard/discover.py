"""Locate SQL files and dbt materialization settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

_SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "dbt_packages",
    "node_modules",
    "target",
}


@dataclass
class DbtProject:
    """The bits of a dbt project that change how a model is built."""

    root: Path
    name: str
    model_paths: list[str] = field(default_factory=lambda: ["models"])
    models_node: dict[str, object] = field(default_factory=dict)
    schema_materializations: dict[str, str] = field(default_factory=dict)


def iter_sql_files(path: Path) -> list[Path]:
    """Return SQL files under a file or directory, skipping build folders."""

    if path.is_file():
        if path.suffix.lower() == ".sql":
            return [path]
        return []
    files: list[Path] = []
    for candidate in path.rglob("*.sql"):
        if not candidate.is_file():
            continue
        if any(part in _SKIP_DIRS for part in candidate.parts):
            continue
        files.append(candidate)
    return sorted(files)


def find_dbt_project(start: Path) -> DbtProject | None:
    """Walk parents until a dbt_project.yml turns up."""

    current = start.resolve()
    if current.is_file():
        current = current.parent
    for parent in [current, *current.parents]:
        project_file = parent / "dbt_project.yml"
        if project_file.is_file():
            return _load_project(project_file)
        if parent.parent == parent:
            break
    return None


def project_materialization(project: DbtProject, file: Path) -> str | None:
    """Return the materialized value dbt_project.yml would apply to a model."""

    parts = _model_parts(project, file)
    if parts is None:
        return None
    node: object = project.models_node
    found: str | None = None
    if isinstance(node, dict):
        top = node.get("+materialized")
        if isinstance(top, str):
            found = top
        for part in parts:
            if not isinstance(node, dict):
                break
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                break
            node = nxt
            materialized = node.get("+materialized")
            if isinstance(materialized, str):
                found = materialized
    if isinstance(found, str):
        return found.lower()
    return None


def in_model_path(project: DbtProject, file: Path) -> bool:
    """Return whether a file lives under one of the project model paths."""

    return _model_parts(project, file) is not None


def relative_path(path: Path, root: Path) -> str:
    """Return a posix path relative to the scan root when possible."""

    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _load_project(project_file: Path) -> DbtProject:
    loaded = yaml.safe_load(project_file.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        loaded = {}
    name = loaded.get("name")
    project_name = name if isinstance(name, str) and name else project_file.parent.name
    model_paths = loaded.get("model-paths") or ["models"]
    if not isinstance(model_paths, list):
        model_paths = ["models"]
    paths = [item for item in model_paths if isinstance(item, str)]
    models = loaded.get("models") or {}
    models_node: dict[str, object] = {}
    if isinstance(models, dict):
        node = models.get(project_name)
        if isinstance(node, dict):
            models_node = node
    root = project_file.parent
    return DbtProject(
        root=root,
        name=project_name,
        model_paths=paths or ["models"],
        models_node=models_node,
        schema_materializations=_schema_materializations(root),
    )


def _schema_materializations(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in list(root.rglob("*.yml")) + list(root.rglob("*.yaml")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.name == "dbt_project.yml":
            continue
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if not isinstance(loaded, dict):
            continue
        models = loaded.get("models")
        if not isinstance(models, list):
            continue
        for item in models:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            config = item.get("config") or {}
            if not isinstance(name, str) or not isinstance(config, dict):
                continue
            materialized = config.get("materialized")
            if isinstance(materialized, str):
                found[name] = materialized.lower()
    return found


def _model_parts(project: DbtProject, file: Path) -> tuple[str, ...] | None:
    for model_path in project.model_paths:
        base = (project.root / model_path).resolve()
        try:
            relative = file.resolve().relative_to(base)
        except ValueError:
            continue
        return relative.with_suffix("").parts
    return None
