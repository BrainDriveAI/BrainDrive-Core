#!/usr/bin/env python3
"""
Register or remove a Library Service runtime row for local testing.

This script intentionally bypasses full plugin lifecycle installation so service
runtime behavior can be tested independently.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text

# Add backend to path for app imports.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))


def _normalize_sqlite_url(url: str) -> Optional[str]:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    raw_path = url[len(prefix) :].strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if path.is_absolute():
        return None
    candidate = (BACKEND_ROOT / raw_path).resolve()
    if candidate.exists():
        return f"{prefix}{candidate.as_posix()}"
    return None


def _bootstrap_env() -> None:
    if os.environ.get("USE_JSON_STORAGE", "").lower() in {"1", "true", "yes", "on"}:
        if not os.environ.get("JSON_DB_PATH"):
            candidate = BACKEND_ROOT / "storage" / "database.json"
            if candidate.exists():
                os.environ["JSON_DB_PATH"] = str(candidate)
        return

    db_url = os.environ.get("DATABASE_URL", "").strip()
    normalized = _normalize_sqlite_url(db_url) if db_url else None
    if normalized:
        os.environ["DATABASE_URL"] = normalized
        return

    if not db_url:
        candidate = BACKEND_ROOT / "braindrive.db"
        if candidate.exists():
            os.environ["DATABASE_URL"] = f"sqlite:///{candidate.as_posix()}"


_bootstrap_env()

from app.core.database import get_db  # type: ignore
from app.models.user import User  # type: ignore

DEFAULT_PLUGIN_SLUG = "BrainDriveLibraryPlugin"
DEFAULT_SERVICE_NAME = "library_service"
DEFAULT_PLUGIN_NAME = "BrainDrive Library Plugin"
DEFAULT_PLUGIN_DESCRIPTION = "Local runtime wiring for BrainDrive Library Plugin service glue."
DEFAULT_PLUGIN_VERSION = "1.1.1-local"
DEFAULT_RUNTIME_DIR_KEY = "Library-Service"
DEFAULT_STATUS = "pending"
DEFAULT_ENV_INHERIT = "minimal"
DEFAULT_DEFINITION_ID = "braindrive_library_service_settings"
DEFAULT_PORT = 18170
DEFAULT_SOURCE_URL = str((PROJECT_ROOT / "PluginBuild" / "Library-Service").resolve())
DEFAULT_INSTALL_COMMAND = "python service_scripts/install_with_venv.py"
DEFAULT_START_COMMAND = "python service_scripts/start_with_venv.py"
DEFAULT_STOP_COMMAND = "python service_scripts/shutdown_with_venv.py"
DEFAULT_RESTART_COMMAND = "python service_scripts/restart_with_venv.py"
SERVICES_RUNTIME_ENV_VAR = "BRAINDRIVE_SERVICES_RUNTIME_DIR"

_RUNTIME_KEY_SANITIZER = re.compile(r"[^A-Za-z0-9._-]+")
_SSH_STYLE_SOURCE = re.compile(r"^[^\s/@]+@[^\s/:]+:.+")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_definition_id(value: Optional[str]) -> Optional[str]:
    normalized = (value or "").strip()
    if not normalized:
        return None
    if normalized.lower() in {"none", "null"}:
        return None
    return normalized


def _build_plugin_id(user_id: str, plugin_slug: str) -> str:
    return f"{user_id}_{plugin_slug}"


def _build_runtime_id(user_id: str, plugin_slug: str, service_name: str) -> str:
    return f"{user_id}_{plugin_slug}_{service_name}"


def _sanitize_runtime_key(value: str) -> str:
    cleaned = _RUNTIME_KEY_SANITIZER.sub("_", value.strip())
    cleaned = cleaned.strip("_")
    return cleaned or "service"


def _runtime_key_from_source_url(source_url: str) -> Optional[str]:
    cleaned = source_url.strip().rstrip("/")
    if not cleaned:
        return None
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    if "://" in cleaned:
        _, remainder = cleaned.split("://", 1)
        path = remainder.split("/", 1)[-1] if "/" in remainder else ""
    else:
        path = cleaned.split(":", 1)[-1]
    if not path:
        return None
    repo = path.split("/")[-1]
    return repo or None


def _resolve_services_runtime_root() -> Path:
    override = str(os.environ.get(SERVICES_RUNTIME_ENV_VAR, "")).strip()
    if override:
        return Path(override).expanduser().resolve()
    return (BACKEND_ROOT / "services_runtime").resolve()


def _resolve_runtime_target_dir(
    *,
    runtime_dir_key: str,
    source_url: str,
    plugin_slug: str,
    service_name: str,
) -> Path:
    runtime_root = _resolve_services_runtime_root()
    explicit_key = (runtime_dir_key or "").strip()
    legacy_key = f"{plugin_slug}_{service_name}" if plugin_slug and service_name else ""
    legacy_dir = (runtime_root / legacy_key).resolve() if legacy_key else None

    if explicit_key:
        explicit_dir = (runtime_root / _sanitize_runtime_key(explicit_key)).resolve()
        if legacy_dir and legacy_dir.exists() and not explicit_dir.exists():
            return legacy_dir
        return explicit_dir

    derived_key = _runtime_key_from_source_url(source_url) or service_name or legacy_key or "service"
    shared_dir = (runtime_root / _sanitize_runtime_key(derived_key)).resolve()
    if legacy_dir and legacy_dir.exists() and not shared_dir.exists():
        return legacy_dir
    return shared_dir


def _is_remote_source_url(value: str) -> bool:
    cleaned = (value or "").strip()
    lowered = cleaned.lower()
    if lowered.startswith(("http://", "https://", "ssh://", "git://")):
        return True
    return bool(_SSH_STYLE_SOURCE.match(cleaned))


def _resolve_local_source_path(source_url: str) -> Path:
    cleaned = (source_url or "").strip()
    if not cleaned:
        raise RuntimeError("source_url cannot be empty when runtime copy is enabled.")
    if _is_remote_source_url(cleaned):
        raise RuntimeError(
            f"source_url '{cleaned}' is remote. Use a local path or pass --skip-runtime-copy."
        )

    if cleaned.startswith("file://"):
        cleaned = cleaned[7:]

    raw = Path(cleaned).expanduser()
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw.resolve())
    else:
        candidates.extend(
            [
                (Path.cwd() / raw).resolve(),
                (PROJECT_ROOT / raw).resolve(),
                (BACKEND_ROOT / raw).resolve(),
            ]
        )

    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise RuntimeError(f"Local source path not found for source_url '{source_url}'.")


def _assert_safe_copy_paths(source_dir: Path, target_dir: Path) -> None:
    source_dir = source_dir.resolve()
    target_dir = target_dir.resolve()
    if source_dir == target_dir:
        raise RuntimeError("Source and runtime target are the same path; refusing to copy.")
    if target_dir in source_dir.parents:
        raise RuntimeError(
            f"Runtime target '{target_dir}' is a parent of source '{source_dir}'; refusing to copy."
        )
    if source_dir in target_dir.parents:
        raise RuntimeError(
            f"Runtime target '{target_dir}' is inside source '{source_dir}'; refusing recursive copy."
        )


def _copy_runtime_from_local_source(source_url: str, target_dir: Path, copy_mode: str) -> dict[str, Any]:
    source_dir = _resolve_local_source_path(source_url)
    if not source_dir.is_dir():
        raise RuntimeError(f"Local source '{source_dir}' is not a directory.")

    _assert_safe_copy_paths(source_dir, target_dir)
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    mode = (copy_mode or "replace").strip().lower()
    ignored = shutil.ignore_patterns(
        ".git",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "*.pyc",
        "*.pyo",
    )

    existed_before = target_dir.exists()
    if mode == "skip" and existed_before:
        return {
            "performed": False,
            "mode": mode,
            "skipped": True,
            "reason": "target_exists",
            "source": str(source_dir),
            "target": str(target_dir),
        }

    if mode == "replace":
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(source_dir, target_dir, ignore=ignored)
    elif mode == "merge":
        shutil.copytree(source_dir, target_dir, dirs_exist_ok=True, ignore=ignored)
    elif mode == "skip":
        shutil.copytree(source_dir, target_dir, ignore=ignored)
    else:
        raise RuntimeError(f"Unsupported runtime copy mode: {copy_mode}")

    return {
        "performed": True,
        "mode": mode,
        "source": str(source_dir),
        "target": str(target_dir),
        "target_exists": target_dir.exists(),
        "service_scripts_present": (target_dir / "service_scripts").exists(),
    }


def _delete_runtime_dir_if_present(target_dir: Path) -> dict[str, Any]:
    if not target_dir.exists():
        return {"deleted": False, "target": str(target_dir), "reason": "not_found"}
    shutil.rmtree(target_dir)
    return {"deleted": True, "target": str(target_dir)}


async def _resolve_user_id(db, user_id: Optional[str], user_email: Optional[str]) -> str:
    if user_id:
        return user_id.strip()
    if not user_email:
        raise RuntimeError("Provide --user-id or --user-email.")

    user = await User.get_by_email(db, user_email.strip())
    if user:
        if isinstance(user, dict):
            resolved = user.get("id") or user.get("user_id")
        else:
            resolved = getattr(user, "id", None)
        if resolved:
            return str(resolved)
    raise RuntimeError(f"No user found with email: {user_email}")


async def _existing_plugin_by_id(db, user_id: str, plugin_id: str):
    result = await db.execute(
        text(
            """
            SELECT id, plugin_slug, required_services_runtime
            FROM plugin
            WHERE id = :plugin_id AND user_id = :user_id
            LIMIT 1
            """
        ),
        {"plugin_id": plugin_id, "user_id": user_id},
    )
    return result.mappings().first()


async def _existing_plugin_by_slug(db, user_id: str, plugin_slug: str):
    result = await db.execute(
        text(
            """
            SELECT id, plugin_slug, required_services_runtime
            FROM plugin
            WHERE plugin_slug = :plugin_slug AND user_id = :user_id
            LIMIT 1
            """
        ),
        {"plugin_slug": plugin_slug, "user_id": user_id},
    )
    return result.mappings().first()


def _parse_json_list(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            return []
    return []


async def _align_plugin_glue_fields(
    db,
    *,
    user_id: str,
    plugin_id: str,
    plugin_slug: str,
    service_name: str,
    required_services_runtime: Any,
) -> None:
    required_services = _parse_json_list(required_services_runtime)
    if service_name not in required_services:
        required_services.append(service_name)
    if not required_services:
        required_services = [service_name]

    await db.execute(
        text(
            """
            UPDATE plugin
            SET
                type = :type,
                plugin_type = :plugin_type,
                enabled = :enabled,
                status = :status,
                scope = :scope,
                endpoints_file = COALESCE(NULLIF(endpoints_file, ''), :endpoints_file),
                route_prefix = COALESCE(NULLIF(route_prefix, ''), :route_prefix),
                required_services_runtime = :required_services_runtime,
                backend_dependencies = COALESCE(backend_dependencies, :backend_dependencies),
                updated_at = :updated_at
            WHERE id = :plugin_id AND user_id = :user_id
            """
        ),
        {
            "type": "fullstack",
            "plugin_type": "fullstack",
            "enabled": True,
            "status": "activated",
            "scope": plugin_slug,
            "endpoints_file": "endpoints.py",
            "route_prefix": "/",
            "required_services_runtime": json.dumps(required_services),
            "backend_dependencies": json.dumps([]),
            "updated_at": _utc_now().isoformat(),
            "plugin_id": plugin_id,
            "user_id": user_id,
        },
    )


async def _ensure_plugin_row(
    db,
    *,
    user_id: str,
    plugin_id: str,
    plugin_slug: str,
    plugin_name: str,
    plugin_description: str,
    plugin_version: str,
    plugin_source_url: str,
    service_name: str,
    skip_plugin_stub: bool,
) -> tuple[str, str]:
    existing = await _existing_plugin_by_id(db, user_id, plugin_id)
    if existing:
        existing_slug = str(existing["plugin_slug"])
        if existing_slug != plugin_slug:
            raise RuntimeError(
                f"Existing plugin id '{plugin_id}' belongs to slug '{existing_slug}', "
                f"not '{plugin_slug}'."
            )
        await _align_plugin_glue_fields(
            db,
            user_id=user_id,
            plugin_id=str(existing["id"]),
            plugin_slug=plugin_slug,
            service_name=service_name,
            required_services_runtime=existing.get("required_services_runtime"),
        )
        return str(existing["id"]), "existing_by_id"

    existing_slug = await _existing_plugin_by_slug(db, user_id, plugin_slug)
    if existing_slug:
        await _align_plugin_glue_fields(
            db,
            user_id=user_id,
            plugin_id=str(existing_slug["id"]),
            plugin_slug=plugin_slug,
            service_name=service_name,
            required_services_runtime=existing_slug.get("required_services_runtime"),
        )
        return str(existing_slug["id"]), "existing_by_slug"

    if skip_plugin_stub:
        raise RuntimeError(
            "Plugin row not found and --skip-plugin-stub was set. "
            "Install plugin first or remove --skip-plugin-stub."
        )

    now_iso = _utc_now().isoformat()
    await db.execute(
        text(
            """
            INSERT INTO plugin (
                id, plugin_slug, name, description, version,
                type, plugin_type, enabled, status, official, author,
                compatibility, scope, is_local, source_type, source_url,
                installation_type, permissions, config_fields, messages,
                dependencies, required_services_runtime, endpoints_file, route_prefix,
                backend_dependencies, created_at, updated_at, user_id
            )
            VALUES (
                :id, :plugin_slug, :name, :description, :version,
                :type, :plugin_type, :enabled, :status, :official, :author,
                :compatibility, :scope, :is_local, :source_type, :source_url,
                :installation_type, :permissions, :config_fields, :messages,
                :dependencies, :required_services_runtime, :endpoints_file, :route_prefix,
                :backend_dependencies, :created_at, :updated_at, :user_id
            )
            """
        ),
        {
            "id": plugin_id,
            "plugin_slug": plugin_slug,
            "name": plugin_name,
            "description": plugin_description,
            "version": plugin_version,
            "type": "fullstack",
            "plugin_type": "fullstack",
            "enabled": True,
            "status": "activated",
            "official": False,
            "author": "BrainDrive Testing",
            "compatibility": "1.0.0",
            "scope": plugin_slug,
            "is_local": True,
            "source_type": "local-testing",
            "source_url": plugin_source_url,
            "installation_type": "local",
            "permissions": json.dumps(["api.access"]),
            "config_fields": json.dumps({}),
            "messages": json.dumps({}),
            "dependencies": json.dumps([]),
            "required_services_runtime": json.dumps([service_name]),
            "endpoints_file": "endpoints.py",
            "route_prefix": "/",
            "backend_dependencies": json.dumps([]),
            "created_at": now_iso,
            "updated_at": now_iso,
            "user_id": user_id,
        },
    )
    return plugin_id, "created_stub"


async def _upsert_runtime_row(db, payload: dict[str, Any]) -> None:
    await db.execute(
        text(
            """
            INSERT INTO plugin_service_runtime (
                id, plugin_id, plugin_slug, name, source_url, type,
                install_command, start_command, stop_command, restart_command,
                healthcheck_url, definition_id, required_env_vars, runtime_dir_key,
                env_inherit, env_overrides, status, created_at, updated_at, user_id
            )
            VALUES (
                :id, :plugin_id, :plugin_slug, :name, :source_url, :type,
                :install_command, :start_command, :stop_command, :restart_command,
                :healthcheck_url, :definition_id, :required_env_vars, :runtime_dir_key,
                :env_inherit, :env_overrides, :status, :created_at, :updated_at, :user_id
            )
            ON CONFLICT(id) DO UPDATE SET
                plugin_id = excluded.plugin_id,
                plugin_slug = excluded.plugin_slug,
                name = excluded.name,
                source_url = excluded.source_url,
                type = excluded.type,
                install_command = excluded.install_command,
                start_command = excluded.start_command,
                stop_command = excluded.stop_command,
                restart_command = excluded.restart_command,
                healthcheck_url = excluded.healthcheck_url,
                definition_id = excluded.definition_id,
                required_env_vars = excluded.required_env_vars,
                runtime_dir_key = excluded.runtime_dir_key,
                env_inherit = excluded.env_inherit,
                env_overrides = excluded.env_overrides,
                status = excluded.status,
                updated_at = excluded.updated_at,
                user_id = excluded.user_id
            """
        ),
        payload,
    )


async def _fetch_runtime_row(db, runtime_id: str):
    result = await db.execute(
        text(
            """
            SELECT
                id, plugin_id, plugin_slug, name, source_url, type,
                install_command, start_command, stop_command, restart_command,
                healthcheck_url, definition_id, required_env_vars,
                runtime_dir_key, env_inherit, env_overrides, status,
                created_at, updated_at, user_id
            FROM plugin_service_runtime
            WHERE id = :runtime_id
            LIMIT 1
            """
        ),
        {"runtime_id": runtime_id},
    )
    row = result.mappings().first()
    if not row:
        return None
    data = dict(row)
    for key in ("required_env_vars", "env_overrides"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            try:
                data[key] = json.loads(value)
            except json.JSONDecodeError:
                pass
    return data


async def _delete_runtime_rows(
    db,
    *,
    user_id: str,
    runtime_id: str,
    plugin_slug: str,
    service_name: str,
) -> int:
    delete_by_id = await db.execute(
        text("DELETE FROM plugin_service_runtime WHERE id = :id AND user_id = :user_id"),
        {"id": runtime_id, "user_id": user_id},
    )
    deleted = int(delete_by_id.rowcount or 0)
    if deleted > 0:
        return deleted

    delete_by_fields = await db.execute(
        text(
            """
            DELETE FROM plugin_service_runtime
            WHERE user_id = :user_id
              AND plugin_slug = :plugin_slug
              AND name = :service_name
            """
        ),
        {
            "user_id": user_id,
            "plugin_slug": plugin_slug,
            "service_name": service_name,
        },
    )
    return int(delete_by_fields.rowcount or 0)


async def _delete_plugin_stub_if_safe(
    db,
    *,
    user_id: str,
    plugin_id: str,
    plugin_slug: str,
) -> dict[str, Any]:
    plugin_row = await _existing_plugin_by_id(db, user_id, plugin_id)
    if not plugin_row:
        return {"deleted": False, "reason": "plugin_not_found"}

    runtime_count_res = await db.execute(
        text(
            "SELECT COUNT(*) FROM plugin_service_runtime WHERE plugin_id = :plugin_id AND user_id = :user_id"
        ),
        {"plugin_id": plugin_id, "user_id": user_id},
    )
    runtime_count = int(runtime_count_res.scalar_one())

    module_count_res = await db.execute(
        text("SELECT COUNT(*) FROM module WHERE plugin_id = :plugin_id AND user_id = :user_id"),
        {"plugin_id": plugin_id, "user_id": user_id},
    )
    module_count = int(module_count_res.scalar_one())

    if runtime_count > 0 or module_count > 0:
        return {
            "deleted": False,
            "reason": "plugin_has_related_rows",
            "runtime_count": runtime_count,
            "module_count": module_count,
        }

    await db.execute(
        text(
            """
            DELETE FROM plugin
            WHERE id = :plugin_id
              AND user_id = :user_id
              AND plugin_slug = :plugin_slug
            """
        ),
        {
            "plugin_id": plugin_id,
            "user_id": user_id,
            "plugin_slug": plugin_slug,
        },
    )
    return {"deleted": True}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install/update/delete Library Service runtime rows and align Library plugin backend glue."
    )
    parser.add_argument("--user-id", help="User ID to target.")
    parser.add_argument("--user-email", help="User email to resolve user ID.")

    parser.add_argument("--plugin-slug", default=DEFAULT_PLUGIN_SLUG, help="Plugin slug.")
    parser.add_argument(
        "--service-name",
        default=DEFAULT_SERVICE_NAME,
        help="Service name stored in plugin_service_runtime.name.",
    )
    parser.add_argument("--plugin-id", default=None, help="Optional explicit plugin ID.")
    parser.add_argument("--runtime-id", default=None, help="Optional explicit runtime row ID.")
    parser.add_argument(
        "--plugin-name",
        default=DEFAULT_PLUGIN_NAME,
        help="Plugin name used when creating a plugin row if missing.",
    )
    parser.add_argument(
        "--plugin-description",
        default=DEFAULT_PLUGIN_DESCRIPTION,
        help="Plugin description used when creating a plugin row if missing.",
    )
    parser.add_argument(
        "--plugin-version",
        default=DEFAULT_PLUGIN_VERSION,
        help="Plugin version used when creating a plugin row if missing.",
    )
    parser.add_argument(
        "--skip-plugin-stub",
        action="store_true",
        help="Fail instead of creating a plugin stub when plugin row is missing.",
    )

    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL, help="Service source URL/path.")
    parser.add_argument(
        "--definition-id",
        default=DEFAULT_DEFINITION_ID,
        help='Definition ID to store (use "none" to store NULL).',
    )
    parser.add_argument("--runtime-dir-key", default=DEFAULT_RUNTIME_DIR_KEY, help="runtime_dir_key value.")
    parser.add_argument("--env-inherit", default=DEFAULT_ENV_INHERIT, help="env_inherit value.")
    parser.add_argument("--status", default=DEFAULT_STATUS, help="Initial runtime status.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Service PROCESS_PORT and health port.")
    parser.add_argument("--health-host", default="localhost", help="Host used for health URL.")

    parser.add_argument("--install-command", default=DEFAULT_INSTALL_COMMAND, help="install_command value.")
    parser.add_argument("--start-command", default=DEFAULT_START_COMMAND, help="start_command value.")
    parser.add_argument("--stop-command", default=DEFAULT_STOP_COMMAND, help="stop_command value.")
    parser.add_argument("--restart-command", default=DEFAULT_RESTART_COMMAND, help="restart_command value.")
    parser.add_argument(
        "--skip-runtime-copy",
        action="store_true",
        help="Skip local source copy into backend/services_runtime before runtime upsert.",
    )
    parser.add_argument(
        "--runtime-copy-mode",
        choices=["replace", "merge", "skip"],
        default="replace",
        help="How to handle an existing runtime directory when copying local source.",
    )

    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete runtime row instead of inserting/updating.",
    )
    parser.add_argument(
        "--delete-plugin-stub",
        action="store_true",
        help="When used with --delete, also delete plugin stub if no related rows remain.",
    )
    parser.add_argument(
        "--delete-runtime-dir",
        action="store_true",
        help="When used with --delete, also remove the resolved runtime directory.",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    plugin_slug = (args.plugin_slug or "").strip()
    service_name = (args.service_name or "").strip()
    source_url = (args.source_url or DEFAULT_SOURCE_URL).strip()
    runtime_dir_key = (args.runtime_dir_key or "").strip()
    if not plugin_slug:
        raise RuntimeError("plugin_slug cannot be empty.")
    if not service_name:
        raise RuntimeError("service_name cannot be empty.")
    if args.port <= 0:
        raise RuntimeError("port must be a positive integer.")
    runtime_target_dir = _resolve_runtime_target_dir(
        runtime_dir_key=runtime_dir_key,
        source_url=source_url,
        plugin_slug=plugin_slug,
        service_name=service_name,
    )

    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        user_id = await _resolve_user_id(db, args.user_id, args.user_email)
        plugin_id = (args.plugin_id or _build_plugin_id(user_id, plugin_slug)).strip()
        runtime_id = (args.runtime_id or _build_runtime_id(user_id, plugin_slug, service_name)).strip()

        if args.delete:
            removed = await _delete_runtime_rows(
                db,
                user_id=user_id,
                runtime_id=runtime_id,
                plugin_slug=plugin_slug,
                service_name=service_name,
            )
            runtime_cleanup = None
            if args.delete_runtime_dir:
                runtime_cleanup = _delete_runtime_dir_if_present(runtime_target_dir)
            plugin_cleanup = None
            if args.delete_plugin_stub:
                plugin_cleanup = await _delete_plugin_stub_if_safe(
                    db,
                    user_id=user_id,
                    plugin_id=plugin_id,
                    plugin_slug=plugin_slug,
                )
            await db.commit()
            print(
                json.dumps(
                    {
                        "action": "delete",
                        "user_id": user_id,
                        "plugin_id": plugin_id,
                        "runtime_id": runtime_id,
                        "deleted_runtime_rows": removed,
                        "runtime_cleanup": runtime_cleanup,
                        "plugin_cleanup": plugin_cleanup,
                    },
                    indent=2,
                    default=str,
                )
            )
            return 0

        runtime_copy = None
        if not args.skip_runtime_copy:
            runtime_copy = _copy_runtime_from_local_source(
                source_url=source_url,
                target_dir=runtime_target_dir,
                copy_mode=args.runtime_copy_mode,
            )

        plugin_id, plugin_source = await _ensure_plugin_row(
            db,
            user_id=user_id,
            plugin_id=plugin_id,
            plugin_slug=plugin_slug,
            plugin_name=args.plugin_name.strip(),
            plugin_description=args.plugin_description.strip(),
            plugin_version=args.plugin_version.strip(),
            plugin_source_url=source_url,
            service_name=service_name,
            skip_plugin_stub=args.skip_plugin_stub,
        )

        port_str = str(args.port)
        now = _utc_now()
        definition_id = _normalize_definition_id(args.definition_id)
        healthcheck_url = f"http://{args.health_host.strip() or 'localhost'}:{port_str}/health"

        payload: dict[str, Any] = {
            "id": runtime_id,
            "plugin_id": plugin_id,
            "plugin_slug": plugin_slug,
            "name": service_name,
            "source_url": source_url,
            "type": "venv_process",
            "install_command": args.install_command.strip(),
            "start_command": args.start_command.strip(),
            "stop_command": args.stop_command.strip(),
            "restart_command": args.restart_command.strip(),
            "healthcheck_url": healthcheck_url,
            "definition_id": definition_id,
            "required_env_vars": json.dumps(["PROCESS_PORT"]),
            "runtime_dir_key": runtime_dir_key,
            "env_inherit": args.env_inherit.strip(),
            "env_overrides": json.dumps({"PROCESS_PORT": port_str}),
            "status": args.status.strip(),
            "created_at": now,
            "updated_at": now,
            "user_id": user_id,
        }
        await _upsert_runtime_row(db, payload)

        runtime_row = await _fetch_runtime_row(db, runtime_id)
        await db.commit()

        print(
            json.dumps(
                {
                    "action": "upsert",
                    "user_id": user_id,
                    "plugin_id": plugin_id,
                    "plugin_source": plugin_source,
                    "runtime_id": runtime_id,
                    "runtime_target_dir": str(runtime_target_dir),
                    "runtime_copy": runtime_copy,
                    "runtime": runtime_row,
                },
                indent=2,
                default=str,
            )
        )
        return 0
    finally:
        try:
            await db_gen.aclose()
        except Exception:
            pass


def main() -> int:
    args = parse_args()
    if not args.user_id and not args.user_email:
        print("Error: provide --user-id or --user-email.", file=sys.stderr)
        return 2
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
