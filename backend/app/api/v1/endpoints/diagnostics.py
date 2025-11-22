from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Any, Dict, List, Optional
from pathlib import Path
import subprocess
import json

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.routers.plugins import plugin_manager

router = APIRouter(tags=["diagnostics"])


def _get_repo_path() -> Path:
    """Best-effort resolve of the repo root for git/version lookups."""
    # endpoints -> v1 -> api -> app -> backend
    return Path(__file__).resolve().parents[3]


def _get_commit_hash() -> Optional[str]:
    """Return the current git commit hash if available."""
    try:
        repo_path = _get_repo_path()
        result = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_path, stderr=subprocess.DEVNULL
        )
        return result.decode().strip()
    except Exception:
        return None


def _get_package_version() -> Optional[str]:
    """Return version from package.json (shared app version), if present."""
    try:
        package_path = _get_repo_path().parent / "package.json"
        if package_path.exists():
            package_data = json.loads(package_path.read_text())
            return package_data.get("version")
    except Exception:
        return None
    return None


async def _get_db_metadata(db: AsyncSession) -> Dict[str, Any]:
    """Collect database engine details and migration version."""
    info: Dict[str, Any] = {"type": settings.DATABASE_TYPE}

    try:
        if settings.DATABASE_TYPE.lower() == "sqlite":
            result = await db.execute(text("select sqlite_version()"))
            info["version"] = result.scalar_one_or_none()
        else:
            result = await db.execute(text("select version()"))
            info["version"] = result.scalar_one_or_none()
    except Exception as exc:  # pragma: no cover - defensive
        info["version_error"] = str(exc)

    try:
        result = await db.execute(
            text("select version_num from alembic_version order by version_num desc limit 1")
        )
        info["migration_version"] = result.scalar_one_or_none()
    except Exception as exc:  # pragma: no cover - defensive
        info["migration_error"] = str(exc)

    return info


async def _get_plugin_summary(user: User) -> Dict[str, Any]:
    """Summarize plugins/modules for the current user."""
    summary: Dict[str, Any] = {"count": 0, "modules": 0, "items": []}
    try:
        if not plugin_manager._initialized:
            await plugin_manager.initialize()

        plugins = await plugin_manager.get_all_plugins_for_designer(user_id=user.id)
        for plugin_id, plugin_data in plugins.items():
            modules = plugin_data.get("modules") or []
            summary["items"].append(
                {
                    "id": plugin_data.get("plugin_slug") or plugin_id,
                    "version": plugin_data.get("version"),
                    "module_count": len(modules),
                    "bundlelocation": plugin_data.get("bundlelocation") or plugin_data.get("bundle_location"),
                    "enabled": plugin_data.get("enabled", True),
                }
            )
            summary["modules"] += len(modules)

        summary["count"] = len(summary["items"])
    except Exception as exc:  # pragma: no cover - defensive
        summary["error"] = str(exc)

    return summary


def _read_log_tail(limit: int = 100) -> Dict[str, Any]:
    """Return a small tail of available log files, if any exist."""
    candidates = [
        _get_repo_path() / "logs" / "app.log",
        _get_repo_path() / "backend.log",
    ]

    for path in candidates:
        if path.exists():
            try:
                lines = path.read_text(errors="ignore").splitlines()
                tail = lines[-limit:] if len(lines) > limit else lines
                return {"path": str(path), "lines": tail}
            except Exception as exc:  # pragma: no cover - defensive
                return {"path": str(path), "error": str(exc)}

    return {"message": "No log file found", "lines": []}


@router.get("/diagnostics")
async def get_diagnostics(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Backend diagnostics surface for issue triage."""
    db_info = await _get_db_metadata(db)
    plugin_summary = await _get_plugin_summary(current_user)
    commit_hash = _get_commit_hash()

    return {
        "app": {
            "name": settings.APP_NAME,
            "environment": settings.APP_ENV,
            "version": _get_package_version(),
            "commit": commit_hash,
        },
        "backend": {
            "db": db_info,
        },
        "plugins": plugin_summary,
        "logs": _read_log_tail(limit=80),
    }
