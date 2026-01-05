#!/usr/bin/env python3
"""
Reset a plugin's service/runtime state for rapid testing.

Actions (in order):
1) Stop plugin services using plugin_service_runtime rows.
2) Remove cloned service repos under backend/services_runtime.
3) Delete plugin records (plugin, module, plugin_service_runtime) and settings rows.
4) Remove shared plugin files under backend/plugins/shared/<plugin_slug>/v<version>.

Use --yes to skip the confirmation prompt.
"""

import argparse
import asyncio
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

from sqlalchemy import text

# Add backend to path for app imports
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))


def _normalize_sqlite_url(url: str) -> Optional[str]:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    raw_path = url[len(prefix):].strip()
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
from app.plugins.repository import PluginRepository  # type: ignore
from app.plugins.service_installler.plugin_service_manager import (  # type: ignore
    stop_plugin_services,
    _resolve_runtime_dir,
)

DEFAULT_PLUGIN_SLUG = "BrainDriveRAGCommunity"


async def fetch_plugin_context(user_id: str, plugin_slug: str) -> Dict[str, Optional[str]]:
    """Gather plugin id/version and service runtimes for the given user/plugin."""
    async for db in get_db():
        repo = PluginRepository(db)
        plugin_data = await repo.get_plugin_by_slug(plugin_slug, user_id)
        plugin_id = plugin_data.get("id") if plugin_data else f"{user_id}_{plugin_slug}"
        plugin_version = plugin_data.get("version") if plugin_data else None
        service_runtimes = await repo.get_service_runtimes_by_plugin_id(plugin_id)
        definition_ids = sorted({svc.definition_id for svc in service_runtimes if svc.definition_id})
        return {
            "plugin_id": plugin_id,
            "plugin_version": plugin_version,
            "service_runtimes": service_runtimes,
            "definition_ids": definition_ids,
        }
    return {"plugin_id": None, "plugin_version": None, "service_runtimes": [], "definition_ids": []}


async def stop_services(
    service_runtimes: List, plugin_slug: str, service_name: Optional[str] = None
) -> Dict[str, str]:
    """Stop services using the shared runtime logic."""
    runtimes = service_runtimes
    if service_name:
        runtimes = [svc for svc in runtimes if svc.name == service_name]
    if not runtimes:
        return {"skipped": "no services to stop"}

    await stop_plugin_services(runtimes, plugin_slug)
    return {"stopped": str(len(runtimes))}


def remove_service_repos(
    service_runtimes: List, plugin_slug: str, service_name: Optional[str] = None
) -> Dict[str, Dict[str, str]]:
    """Delete cloned service repos for a clean slate."""
    removed: Dict[str, Dict[str, str]] = {}
    seen: Set[Path] = set()

    def _on_rm_error(func, path, exc_info) -> None:
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            raise

    for svc in service_runtimes:
        if service_name and svc.name != service_name:
            continue
        repo_dir = _resolve_runtime_dir(svc, plugin_slug).resolve()
        if repo_dir in seen:
            continue
        seen.add(repo_dir)
        if repo_dir.exists():
            try:
                shutil.rmtree(repo_dir, onerror=_on_rm_error)
                removed[str(repo_dir)] = {"removed": "true"}
            except Exception as exc:  # pragma: no cover - defensive
                removed[str(repo_dir)] = {"removed": "false", "error": str(exc)}
        else:
            removed[str(repo_dir)] = {"removed": "false", "reason": "not found"}
    return removed


def remove_shared_plugin_files(plugin_slug: str, plugin_version: Optional[str]) -> Dict[str, str]:
    """Remove the shared plugin copy created during install."""
    if not plugin_version:
        return {"removed": "false", "reason": "plugin version unavailable"}
    shared_path = BACKEND_ROOT / "plugins" / "shared" / plugin_slug / f"v{plugin_version}"
    if not shared_path.exists():
        return {"removed": "false", "path": str(shared_path), "reason": "not found"}
    try:
        shutil.rmtree(shared_path)
        return {"removed": "true", "path": str(shared_path)}
    except Exception as exc:  # pragma: no cover - defensive
        return {"removed": "false", "path": str(shared_path), "error": str(exc)}


async def wipe_db(user_id: str, plugin_slug: str, plugin_id: str, definition_ids: List[str]) -> dict:
    """Remove plugin/module/service/settings rows for the plugin."""
    db_gen = get_db()
    session = await db_gen.__anext__()
    try:
        await session.execute(
            text("DELETE FROM module WHERE plugin_id = :plugin_id AND user_id = :user_id"),
            {"plugin_id": plugin_id, "user_id": user_id},
        )
        await session.execute(
            text("DELETE FROM plugin_service_runtime WHERE plugin_id = :plugin_id AND user_id = :user_id"),
            {"plugin_id": plugin_id, "user_id": user_id},
        )
        await session.execute(
            text("DELETE FROM plugin_service_runtime WHERE plugin_slug = :slug AND user_id = :user_id"),
            {"slug": plugin_slug, "user_id": user_id},
        )
        await session.execute(
            text("DELETE FROM plugin WHERE id = :plugin_id AND user_id = :user_id"),
            {"plugin_id": plugin_id, "user_id": user_id},
        )

        definitions_removed = []
        for definition_id in definition_ids:
            await session.execute(
                text("DELETE FROM settings_instances WHERE definition_id = :def_id AND user_id = :user_id"),
                {"def_id": definition_id, "user_id": user_id},
            )
            count_res = await session.execute(
                text("SELECT COUNT(*) FROM settings_instances WHERE definition_id = :def_id"),
                {"def_id": definition_id},
            )
            remaining = count_res.scalar_one()
            if remaining == 0:
                await session.execute(
                    text("DELETE FROM settings_definitions WHERE id = :def_id"),
                    {"def_id": definition_id},
                )
                definitions_removed.append(definition_id)

        await session.commit()
        return {
            "success": True,
            "plugin_id": plugin_id,
            "definitions_removed": definitions_removed,
        }
    except Exception as exc:  # pragma: no cover - defensive
        await session.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        try:
            await db_gen.aclose()
        except Exception:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset plugin state (services + DB + shared files).")
    parser.add_argument("--user-id", help="User ID the plugin was installed for.")
    parser.add_argument("--user-email", help="User email for lookup when user ID is unknown.")
    parser.add_argument("--plugin-slug", default=DEFAULT_PLUGIN_SLUG, help="Plugin slug to reset.")
    parser.add_argument("--plugin-version", default=None, help="Override plugin version for shared files cleanup.")
    parser.add_argument("--service-name", default=None, help="Optional single service name to target.")
    parser.add_argument("--skip-stop", action="store_true", help="Skip stopping services.")
    parser.add_argument("--skip-db", action="store_true", help="Skip database cleanup.")
    parser.add_argument("--skip-repos", action="store_true", help="Skip deleting service repos.")
    parser.add_argument("--yes", action="store_true", help="Do not prompt for confirmation.")
    return parser.parse_args()


async def resolve_user_id(user_id: Optional[str], user_email: Optional[str]) -> str:
    if user_id:
        return user_id
    if not user_email:
        raise RuntimeError("Provide --user-id or --user-email.")
    async for db in get_db():
        user = await User.get_by_email(db, user_email)
        if user:
            if isinstance(user, dict):
                resolved = user.get("id") or user.get("user_id")
            else:
                resolved = getattr(user, "id", None)
            if resolved:
                return str(resolved)
        break
    raise RuntimeError(f"No user found with email: {user_email}")


async def run(args: argparse.Namespace) -> int:
    user_id = await resolve_user_id(args.user_id, args.user_email)
    context = await fetch_plugin_context(user_id, args.plugin_slug)
    plugin_id = context.get("plugin_id") or f"{user_id}_{args.plugin_slug}"
    plugin_version = args.plugin_version or context.get("plugin_version")
    service_runtimes = context.get("service_runtimes") or []
    definition_ids = context.get("definition_ids") or []

    if not args.skip_stop:
        stop_result = await stop_services(service_runtimes, args.plugin_slug, args.service_name)
        print("Stop results:", stop_result)
    else:
        print("Skipping service stop (--skip-stop).")

    if not args.skip_repos:
        removed = remove_service_repos(service_runtimes, args.plugin_slug, args.service_name)
        print("Repo removal:", removed)
    else:
        print("Skipping repo removal (--skip-repos).")

    shared_result = remove_shared_plugin_files(args.plugin_slug, plugin_version)
    print("Shared plugin cleanup:", shared_result)

    if not args.skip_db:
        db_result = await wipe_db(user_id, args.plugin_slug, plugin_id, definition_ids)
        print("DB cleanup:", db_result)
    else:
        print("Skipping DB cleanup (--skip-db).")

    print("Reset complete.")
    return 0


def main() -> int:
    args = parse_args()
    if not args.user_id and not args.user_email:
        print("Error: provide --user-id or --user-email.")
        return 2
    user_label = args.user_id or args.user_email
    if not args.yes:
        prompt = (
            f"This will stop services, remove cloned repos, and delete DB rows for {args.plugin_slug} "
            f"for user '{user_label}'. Proceed? [y/N]: "
        )
        if input(prompt).strip().lower() != "y":
            print("Aborted.")
            return 1
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
