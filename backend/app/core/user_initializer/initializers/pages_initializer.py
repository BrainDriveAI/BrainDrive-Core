"""
Pages initializer plugin.

This plugin initializes starter pages for a new user.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from typing import Any, Dict

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.user_initializer.base import UserInitializerBase
from app.core.user_initializer.registry import register_initializer
from app.core.user_initializer.utils import generate_uuid

logger = logging.getLogger(__name__)


class PagesInitializer(UserInitializerBase):
    """Initializer for starter user pages."""

    name = "pages_initializer"
    description = "Initializes default pages for a new user"
    priority = 600
    dependencies = [
        "navigation_initializer",
        "github_plugin_initializer",
        "library_onboarding_initializer",
    ]

    STARTER_PAGE_SPECS: tuple[Dict[str, Any], ...] = ()

    async def get_module_ids(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Get the module IDs for a user's BrainDrive Chat plugin."""
        try:
            plugin_stmt = text(
                """
                SELECT id FROM plugin
                WHERE user_id = :user_id AND plugin_slug = 'BrainDriveChat'
                """
            )
            plugin_result = await db.execute(plugin_stmt, {"user_id": user_id})
            plugin_id = plugin_result.scalar_one_or_none()
            if not plugin_id:
                logger.error("BrainDriveChat plugin not found for user %s", user_id)
                return {}

            module_stmt = text(
                """
                SELECT id, name FROM module
                WHERE user_id = :user_id AND plugin_id = :plugin_id
                """
            )
            module_result = await db.execute(
                module_stmt,
                {
                    "user_id": user_id,
                    "plugin_id": plugin_id,
                },
            )

            module_ids: Dict[str, str] = {}
            for row in module_result:
                module_ids[str(row.name)] = str(row.id)

            return {"plugin_id": str(plugin_id), "module_ids": module_ids}
        except Exception as exc:
            logger.error("Error getting module IDs for user %s: %s", user_id, exc)
            return {}

    def _build_module_args(self, module_id: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        args: Dict[str, Any] = {
            "moduleId": module_id,
            "displayName": f"{spec['name']} Chat",
            "conversation_type": spec["conversation_type"],
            "default_library_scope_enabled": bool(
                spec["default_library_scope_enabled"]
            ),
            "default_project_slug": spec.get("default_project_slug"),
            "default_project_lifecycle": spec.get(
                "default_project_lifecycle", "active"
            ),
            "apply_defaults_on_new_chat": True,
            "lock_project_scope": False,
            "lock_persona_selection": False,
            "lock_model_selection": False,
            # Forward-compatible root-aware hints for life/project scoping.
            "default_scope_root": spec.get("default_scope_root"),
            "default_scope_path": spec.get("default_scope_path"),
        }
        return args

    def _normalize_route_slug(self, route_slug: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", str(route_slug).strip().lower())
        normalized = re.sub(r"-+", "-", normalized).strip("-")
        return normalized or "capture"

    async def _starter_page_exists(
        self, db: AsyncSession, user_id: str, page_name: str
    ) -> bool:
        stmt = text(
            """
            SELECT id FROM pages
            WHERE creator_id = :user_id AND lower(name) = lower(:name)
            LIMIT 1
            """
        )
        result = await db.execute(stmt, {"user_id": user_id, "name": page_name})
        return result.scalar_one_or_none() is not None

    async def _resolve_unique_route(
        self, db: AsyncSession, user_id: str, base_route: str
    ) -> str:
        normalized = self._normalize_route_slug(base_route)
        route = normalized
        suffix = 2
        stmt = text(
            """
            SELECT id FROM pages
            WHERE creator_id = :user_id AND route = :route
            LIMIT 1
            """
        )

        while True:
            result = await db.execute(stmt, {"user_id": user_id, "route": route})
            if result.scalar_one_or_none() is None:
                return route
            route = f"{normalized}-{suffix}"
            suffix += 1

    def _build_page_content(self, module_id: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        timestamp_base = int(datetime.datetime.now().timestamp() * 1000)
        chat_interface_id = f"BrainDriveChat_{module_id}_{timestamp_base}_{generate_uuid()[:8]}"
        args = self._build_module_args(module_id, spec)

        layout_item = {
            "i": chat_interface_id,
            "x": 0,
            "y": 0,
            "w": 12,
            "h": 10,
            "pluginId": "BrainDriveChat",
            "args": args,
        }

        return {
            "layouts": {
                "desktop": [layout_item],
                "tablet": [{**layout_item, "w": 4, "h": 3}],
                "mobile": [{**layout_item, "w": 4, "h": 3}],
            },
            "modules": {},
        }

    async def initialize(self, user_id: str, db: AsyncSession, **kwargs) -> bool:
        """Initialize starter pages for a new user."""
        del kwargs

        try:
            logger.info("Initializing starter pages for user %s", user_id)
            if not self.STARTER_PAGE_SPECS:
                logger.info("No starter pages configured; skipping page initialization for user %s", user_id)
                return True
            module_info = await self.get_module_ids(user_id, db)
            if not module_info:
                return False

            module_ids: Dict[str, str] = module_info.get("module_ids", {})
            chat_module_id = None
            for module_name, module_id in module_ids.items():
                if "brainDrivechat" in module_name.lower() or "chat" in module_name.lower():
                    chat_module_id = module_id
                    break

            if not chat_module_id:
                logger.error("BrainDriveChat module not found for user %s", user_id)
                return False

            insert_stmt = text(
                """
                INSERT INTO pages
                (id, name, route, content, creator_id, created_at, updated_at, is_published, publish_date, description)
                VALUES
                (:id, :name, :route, :content, :creator_id, :created_at, :updated_at, :is_published, :publish_date, :description)
                """
            )

            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            created_count = 0
            for spec in self.STARTER_PAGE_SPECS:
                if await self._starter_page_exists(db, user_id, spec["name"]):
                    logger.info(
                        "Starter page '%s' already exists for user %s; skipping",
                        spec["name"],
                        user_id,
                    )
                    continue

                route = await self._resolve_unique_route(db, user_id, spec["route_slug"])
                content = self._build_page_content(chat_module_id, spec)

                await db.execute(
                    insert_stmt,
                    {
                        "id": generate_uuid(),
                        "name": spec["name"],
                        "route": route,
                        "content": json.dumps(content),
                        "creator_id": user_id,
                        "created_at": current_time,
                        "updated_at": current_time,
                        "is_published": 1,
                        "publish_date": current_time,
                        "description": spec.get("description", ""),
                    },
                )
                created_count += 1
                logger.info(
                    "Created starter page '%s' (route=%s) for user %s",
                    spec["name"],
                    route,
                    user_id,
                )

            await db.commit()
            logger.info(
                "Starter pages initialized for user %s (created=%s)",
                user_id,
                created_count,
            )
            return True
        except Exception as exc:
            logger.error("Error initializing pages for user %s: %s", user_id, exc)
            await db.rollback()
            return False

    async def cleanup(self, user_id: str, db: AsyncSession, **kwargs) -> bool:
        """Clean up pages if initialization fails."""
        del kwargs

        try:
            if not self.STARTER_PAGE_SPECS:
                logger.info("No starter pages configured; skipping pages cleanup for user %s", user_id)
                return True

            page_names = [spec["name"] for spec in self.STARTER_PAGE_SPECS if spec.get("name")]
            if not page_names:
                logger.info("Starter page cleanup found no page names for user %s", user_id)
                return True

            stmt = text(
                """
                DELETE FROM pages
                WHERE creator_id = :user_id
                AND lower(name) IN :page_names
                """
            ).bindparams(bindparam("page_names", expanding=True))
            await db.execute(
                stmt,
                {
                    "user_id": user_id,
                    "page_names": [name.lower() for name in page_names],
                },
            )
            await db.commit()
            logger.info("Pages cleanup successful for user %s", user_id)
            return True
        except Exception as exc:
            logger.error("Error during pages cleanup for user %s: %s", user_id, exc)
            await db.rollback()
            return False


register_initializer(PagesInitializer)
