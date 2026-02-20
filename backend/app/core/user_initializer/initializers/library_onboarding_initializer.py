"""
Library onboarding initializer plugin.

Seeds the new user-scoped Library filesystem from a canonical base template.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.user_initializer.base import UserInitializerBase
from app.core.user_initializer.library_template import (
    apply_canonical_schema,
    copy_base_template_idempotent,
    resolve_base_template_path,
    resolve_library_root_path,
)
from app.core.user_initializer.registry import register_initializer

logger = logging.getLogger(__name__)

_VALID_USER_ID = re.compile(r"^[A-Za-z0-9_]{3,128}$")


class LibraryOnboardingInitializer(UserInitializerBase):
    """Initializer for deterministic user library onboarding scaffold."""

    name = "library_onboarding_initializer"
    description = "Initializes user-scoped library structure and onboarding state"
    priority = 550
    dependencies = ["github_plugin_initializer"]

    async def initialize(self, user_id: str, db: AsyncSession, **kwargs) -> bool:
        del db, kwargs  # Filesystem-only initializer.

        try:
            library_root = resolve_library_root_path()
            scoped_root = self._resolve_scoped_library_root(library_root, user_id)
            template_root = resolve_base_template_path()

            scoped_root.mkdir(parents=True, exist_ok=True)
            copy_result = copy_base_template_idempotent(template_root, scoped_root)
            schema_result = apply_canonical_schema(scoped_root)

            logger.info(
                "Library onboarding completed for user %s (copied_files=%s skipped_files=%s schema_changes=%s)",
                user_id,
                len(copy_result.copied_files),
                len(copy_result.skipped_files),
                len(getattr(schema_result, "changed_paths", []) or []),
            )
            return True
        except Exception as exc:
            logger.error("Library onboarding initializer failed for user %s: %s", user_id, exc)
            return False

    async def cleanup(self, user_id: str, db: AsyncSession, **kwargs) -> bool:
        del db, kwargs

        try:
            library_root = resolve_library_root_path()
            scoped_root = self._resolve_scoped_library_root(library_root, user_id)
            if scoped_root.exists():
                shutil.rmtree(scoped_root)
            return True
        except Exception as exc:
            logger.error("Library onboarding cleanup failed for user %s: %s", user_id, exc)
            return False

    def _resolve_scoped_library_root(self, library_root: str | Path, user_id: str) -> Path:
        normalized_user_id = self._normalize_user_id(user_id)
        root = Path(library_root).expanduser().resolve()
        return root / "users" / normalized_user_id

    def _normalize_user_id(self, raw_user_id: str) -> str:
        normalized = str(raw_user_id).strip().replace("-", "")
        if not normalized or not _VALID_USER_ID.fullmatch(normalized):
            raise ValueError(f"Invalid user_id for library onboarding: {raw_user_id}")
        return normalized


register_initializer(LibraryOnboardingInitializer)
