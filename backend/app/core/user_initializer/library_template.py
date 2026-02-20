"""
Utilities for resolving and copying the canonical base library template.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any


ENV_LIBRARY_PATH = "BRAINDRIVE_LIBRARY_PATH"
ENV_BASE_TEMPLATE_PATH = "BRAINDRIVE_LIBRARY_BASE_TEMPLATE_PATH"
ENV_LIBRARY_SERVICE_ENV_PATH = "BRAINDRIVE_LIBRARY_SERVICE_ENV_PATH"
ENV_LIBRARY_SCHEMA_MODULE_PATH = "BRAINDRIVE_LIBRARY_SCHEMA_MODULE_PATH"

DEFAULT_BASE_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[3]
    / "services_runtime"
    / "Library-Service"
    / "library_templates"
    / "Base_Library"
)
DEFAULT_LIBRARY_SERVICE_ENV_PATH = (
    Path(__file__).resolve().parents[3] / "services_runtime" / "Library-Service" / ".env"
)
DEFAULT_LIBRARY_SCHEMA_MODULE_PATH = (
    Path(__file__).resolve().parents[3]
    / "services_runtime"
    / "Library-Service"
    / "app"
    / "library_schema.py"
)

_SHARED_SCHEMA_CACHE: tuple[str, ModuleType] | None = None


@dataclass(frozen=True)
class TemplateCopyResult:
    """Tracks copied/missing files for deterministic onboarding diagnostics."""

    source: Path
    destination: Path
    created_directories: tuple[str, ...]
    copied_files: tuple[str, ...]
    skipped_files: tuple[str, ...]


def _resolve_service_env_path(configured_path: str | Path | None = None) -> Path:
    candidate = (
        configured_path
        or os.environ.get(ENV_LIBRARY_SERVICE_ENV_PATH)
        or DEFAULT_LIBRARY_SERVICE_ENV_PATH
    )
    return Path(str(candidate)).expanduser().resolve()


def _read_service_env_values(
    configured_env_path: str | Path | None = None,
) -> tuple[dict[str, str], Path]:
    env_path = _resolve_service_env_path(configured_env_path)
    if not env_path.is_file():
        return {}, env_path

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and (
            (value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")
        ):
            value = value[1:-1]
        values[key] = value
    return values, env_path


def _first_nonempty(*candidates: str | Path | None) -> str:
    for candidate in candidates:
        if candidate is None:
            continue
        normalized = str(candidate).strip()
        if normalized:
            return normalized
    return ""


def _resolve_candidate_path(
    candidate: str | Path,
    relative_root: Path | None = None,
) -> Path:
    resolved = Path(str(candidate)).expanduser()
    if not resolved.is_absolute() and relative_root is not None:
        resolved = relative_root / resolved
    return resolved.resolve()


def resolve_library_root_path(
    configured_path: str | Path | None = None,
    configured_env_path: str | Path | None = None,
) -> Path:
    """
    Resolve the user library root with explicit-path > env > Library-Service .env precedence.
    """
    direct_candidate = _first_nonempty(configured_path, os.environ.get(ENV_LIBRARY_PATH))
    if direct_candidate:
        return _resolve_candidate_path(direct_candidate)

    service_env_values, service_env_path = _read_service_env_values(configured_env_path)
    service_candidate = _first_nonempty(service_env_values.get(ENV_LIBRARY_PATH))
    if service_candidate:
        return _resolve_candidate_path(service_candidate, service_env_path.parent)

    raise ValueError(
        "Library root path is not configured; set BRAINDRIVE_LIBRARY_PATH or "
        "provide it in the Library-Service .env file."
    )


def resolve_base_template_path(
    configured_path: str | Path | None = None,
    configured_env_path: str | Path | None = None,
) -> Path:
    """
    Resolve the base template path with explicit-path > env > service-env > repo default precedence.
    """
    direct_candidate = _first_nonempty(
        configured_path,
        os.environ.get(ENV_BASE_TEMPLATE_PATH),
    )
    if direct_candidate:
        resolved = _resolve_candidate_path(direct_candidate)
    else:
        service_env_values, service_env_path = _read_service_env_values(configured_env_path)
        service_candidate = _first_nonempty(service_env_values.get(ENV_BASE_TEMPLATE_PATH))
        if service_candidate:
            resolved = _resolve_candidate_path(service_candidate, service_env_path.parent)
        else:
            resolved = DEFAULT_BASE_TEMPLATE_PATH.resolve()

    if not resolved.is_dir():
        raise FileNotFoundError(
            f"Base library template path does not exist: {resolved}"
        )
    return resolved


def resolve_library_schema_module_path(
    configured_path: str | Path | None = None,
    configured_env_path: str | Path | None = None,
) -> Path:
    """Resolve the shared schema module path used by runtime + registration bootstrap."""
    direct_candidate = _first_nonempty(
        configured_path,
        os.environ.get(ENV_LIBRARY_SCHEMA_MODULE_PATH),
    )
    if direct_candidate:
        resolved = _resolve_candidate_path(direct_candidate)
    else:
        service_env_values, service_env_path = _read_service_env_values(configured_env_path)
        service_candidate = _first_nonempty(
            service_env_values.get(ENV_LIBRARY_SCHEMA_MODULE_PATH)
        )
        if service_candidate:
            resolved = _resolve_candidate_path(service_candidate, service_env_path.parent)
        else:
            resolved = DEFAULT_LIBRARY_SCHEMA_MODULE_PATH.resolve()

    if not resolved.is_file():
        raise FileNotFoundError(
            f"Library schema module path does not exist: {resolved}"
        )
    return resolved


def load_library_schema_module(
    configured_path: str | Path | None = None,
    configured_env_path: str | Path | None = None,
) -> ModuleType:
    """Dynamically load the shared runtime schema module for initializer parity."""
    global _SHARED_SCHEMA_CACHE

    module_path = resolve_library_schema_module_path(
        configured_path=configured_path,
        configured_env_path=configured_env_path,
    )
    cache_key = module_path.as_posix()

    if _SHARED_SCHEMA_CACHE and _SHARED_SCHEMA_CACHE[0] == cache_key:
        return _SHARED_SCHEMA_CACHE[1]

    spec = importlib.util.spec_from_file_location("braindrive_shared_library_schema", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load schema module from {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    _SHARED_SCHEMA_CACHE = (cache_key, module)
    return module


def apply_canonical_schema(
    scoped_root: Path,
    *,
    include_digest_period_files: bool = True,
    configured_schema_module_path: str | Path | None = None,
    configured_env_path: str | Path | None = None,
) -> Any:
    """Apply canonical library structure via the shared runtime schema module."""
    schema_module = load_library_schema_module(
        configured_path=configured_schema_module_path,
        configured_env_path=configured_env_path,
    )

    ensure_fn = getattr(schema_module, "ensure_scoped_library_structure", None)
    if not callable(ensure_fn):
        raise AttributeError(
            "Shared schema module is missing ensure_scoped_library_structure"
        )

    return ensure_fn(
        Path(scoped_root),
        include_digest_period_files=include_digest_period_files,
    )


def copy_base_template_idempotent(
    source_root: Path, destination_root: Path
) -> TemplateCopyResult:
    """
    Copy the base template into destination without overwriting existing files.
    """
    source_root = source_root.expanduser().resolve()
    destination_root = destination_root.expanduser().resolve()

    if not source_root.is_dir():
        raise FileNotFoundError(
            f"Base library template path does not exist: {source_root}"
        )

    created_directories: list[str] = []
    copied_files: list[str] = []
    skipped_files: list[str] = []

    for path in sorted(source_root.rglob("*")):
        relative_path = path.relative_to(source_root)
        target = destination_root / relative_path

        if path.is_dir():
            if not target.exists():
                target.mkdir(parents=True, exist_ok=True)
                created_directories.append(relative_path.as_posix())
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            skipped_files.append(relative_path.as_posix())
            continue

        shutil.copy2(path, target)
        copied_files.append(relative_path.as_posix())

    return TemplateCopyResult(
        source=source_root,
        destination=destination_root,
        created_directories=tuple(created_directories),
        copied_files=tuple(copied_files),
        skipped_files=tuple(skipped_files),
    )
