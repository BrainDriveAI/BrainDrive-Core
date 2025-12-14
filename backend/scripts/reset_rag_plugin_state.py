#!/usr/bin/env python3
"""
Reset the BrainDrive RAG Community plugin state for rapid testing.

Actions (in order):
1) Stop venv services (Document Chat, Document Processing) via shutdown scripts.
2) Remove cloned service repos under backend/services_runtime/.
3) Delete plugin records (plugin, module, plugin_service_runtime) and settings rows for the RAG plugin.
4) Remove shared plugin files under backend/plugins/shared/BrainDriveRAGCommunity/v0.1.0.

Use --yes to skip the confirmation prompt.
"""

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

# Add backend to path for app imports
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import get_db  # type: ignore


PLUGIN_SLUG = "BrainDriveRAGCommunity"
PLUGIN_VERSION = "0.1.0"
SETTINGS_DEFINITION_ID = "braindrive_rag_service_settings"
PLUGIN_ID_TEMPLATE = "{user_id}_" + PLUGIN_SLUG

RUNTIME_BASE = BACKEND_ROOT / "services_runtime"

SERVICES = {
    "document_chat": {
        "repo": RUNTIME_BASE / "Document-Chat-Service",
        "shutdown": "service_scripts/shutdown_with_venv.py",
    },
    "document_processing": {
        "repo": RUNTIME_BASE / "Document-Processing-Service",
        "shutdown": "service_scripts/shutdown_with_venv.py",
    },
}


def find_python(repo: Path) -> str:
    """Prefer the repo's venv python if present; fallback to python3.11."""
    venv_python = repo / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return os.environ.get("PYTHON_BIN", "python3.11")


def stop_services() -> dict:
    """Invoke shutdown scripts for known services; ignore failures."""
    results = {}
    for key, meta in SERVICES.items():
        repo = meta["repo"]
        script = repo / meta["shutdown"]
        if not script.exists():
            results[key] = {"skipped": True, "reason": "shutdown script missing"}
            continue
        python_bin = find_python(repo)
        completed = subprocess.run(
            [python_bin, str(script)],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        results[key] = {
            "success": completed.returncode == 0,
            "code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    return results


def remove_service_repos() -> dict:
    """Delete cloned service repos for a clean slate."""
    removed = {}
    for key, meta in SERVICES.items():
        repo = meta["repo"]
        if repo.exists():
            try:
                shutil.rmtree(repo)
                removed[key] = {"removed": True, "path": str(repo)}
            except Exception as exc:  # pragma: no cover - defensive
                removed[key] = {"removed": False, "path": str(repo), "error": str(exc)}
        else:
            removed[key] = {"removed": False, "path": str(repo), "reason": "not found"}
    return removed


def remove_shared_plugin_files() -> dict:
    """Remove the shared plugin copy created during install."""
    shared_path = BACKEND_ROOT.parent / "backend" / "plugins" / "shared" / PLUGIN_SLUG / f"v{PLUGIN_VERSION}"
    if not shared_path.exists():
        return {"removed": False, "path": str(shared_path), "reason": "not found"}
    try:
        shutil.rmtree(shared_path)
        return {"removed": True, "path": str(shared_path)}
    except Exception as exc:  # pragma: no cover - defensive
        return {"removed": False, "path": str(shared_path), "error": str(exc)}


async def wipe_db(user_id: str) -> dict:
    """Remove plugin/module/service/settings rows for the RAG plugin."""
    db_gen = get_db()
    session = await db_gen.__anext__()
    plugin_id = PLUGIN_ID_TEMPLATE.format(user_id=user_id)
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
            {"slug": PLUGIN_SLUG, "user_id": user_id},
        )
        await session.execute(
            text("DELETE FROM plugin WHERE id = :plugin_id AND user_id = :user_id"),
            {"plugin_id": plugin_id, "user_id": user_id},
        )
        # Settings instance tied to the RAG plugin
        await session.execute(
            text("DELETE FROM settings_instances WHERE id = :instance_id AND user_id = :user_id"),
            {"instance_id": f"rag_services_settings_{user_id}", "user_id": user_id},
        )
        # Remove definition if no other instances exist
        count_res = await session.execute(
            text("SELECT COUNT(*) FROM settings_instances WHERE definition_id = :def_id"),
            {"def_id": SETTINGS_DEFINITION_ID},
        )
        remaining = count_res.scalar_one()
        if remaining == 0:
            await session.execute(
                text("DELETE FROM settings_definitions WHERE id = :def_id"),
                {"def_id": SETTINGS_DEFINITION_ID},
            )
        await session.commit()
        return {
            "success": True,
            "plugin_id": plugin_id,
            "definition_deleted": remaining == 0,
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
    parser = argparse.ArgumentParser(description="Reset RAG plugin state (services + DB + shared files).")
    parser.add_argument("--user-id", required=True, help="User ID the plugin was installed for.")
    parser.add_argument("--skip-stop", action="store_true", help="Skip calling shutdown scripts.")
    parser.add_argument("--skip-db", action="store_true", help="Skip database cleanup.")
    parser.add_argument("--skip-repos", action="store_true", help="Skip deleting service repos.")
    parser.add_argument("--yes", action="store_true", help="Do not prompt for confirmation.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.yes:
        prompt = (
            f"This will stop services, remove cloned repos, and delete DB rows for {PLUGIN_SLUG} "
            f"for user '{args.user_id}'. Proceed? [y/N]: "
        )
        if input(prompt).strip().lower() != "y":
            print("Aborted.")
            return 1

    if not args.skip_stop:
        stop_result = stop_services()
        print("Stop results:", stop_result)
    else:
        print("Skipping service stop (--skip-stop).")

    if not args.skip_repos:
        removed = remove_service_repos()
        print("Repo removal:", removed)
    else:
        print("Skipping repo removal (--skip-repos).")

    shared_result = remove_shared_plugin_files()
    print("Shared plugin cleanup:", shared_result)

    if not args.skip_db:
        db_result = asyncio.run(wipe_db(args.user_id))
        print("DB cleanup:", db_result)
    else:
        print("Skipping DB cleanup (--skip-db).")

    print("Reset complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
