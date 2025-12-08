import logging
import json
from datetime import datetime
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.core.user_updater.base import UserUpdaterBase
from app.core.user_updater.registry import register_updater

logger = logging.getLogger(__name__)


class SettingsToV065(UserUpdaterBase):
    """Update users from version 0.6.0 to 0.6.5 by adding white-label settings."""

    name = "settings_to_v065"
    description = "Add white-label settings for branding links"
    from_version = "0.6.0"
    to_version = "0.6.5"
    priority = 1300

    WHITE_LABEL_ID = "white_label_settings"
    WHITE_LABEL_NAME = "White Label"
    WHITE_LABEL_DEFAULT = {
        "PRIMARY": {"label": "BrainDrive", "url": "https://tinyurl.com/4dx47m7p"},
        "OWNERS_MANUAL": {
            "label": "BrainDrive Owner's Manual",
            "url": "https://tinyurl.com/vd99cuex",
        },
        "COMMUNITY": {
            "label": "BrainDrive Community",
            "url": "https://tinyurl.com/yc2u5v2a",
        },
        "SUPPORT": {"label": "BrainDrive Support", "url": "https://tinyurl.com/4h4rtx2m"},
        "DOCUMENTATION": {
            "label": "BrainDrive Docs",
            "url": "https://tinyurl.com/ewajc7k3",
        },
    }

    async def _ensure_definition(self, db: AsyncSession) -> None:
        check_stmt = text("SELECT id FROM settings_definitions WHERE id = :id")
        res = await db.execute(check_stmt, {"id": self.WHITE_LABEL_ID})
        if res.scalar_one_or_none():
            return

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        insert_stmt = text(
            """
            INSERT INTO settings_definitions
            (id, name, description, category, type, default_value, allowed_scopes, validation, is_multiple, tags, created_at, updated_at)
            VALUES
            (:id, :name, :description, :category, :type, :default_value, :allowed_scopes, :validation, :is_multiple, :tags, :created_at, :updated_at)
            """
        )
        await db.execute(
            insert_stmt,
            {
                "id": self.WHITE_LABEL_ID,
                "name": self.WHITE_LABEL_NAME,
                "description": "Labels and links for product/brand surfaces",
                "category": "branding",
                "type": "object",
                "default_value": json.dumps(self.WHITE_LABEL_DEFAULT),
                "allowed_scopes": '["system", "user"]',
                "validation": None,
                "is_multiple": False,
                "tags": '["auto_generated", "ui", "branding"]',
                "created_at": current_time,
                "updated_at": current_time,
            },
        )
        logger.info("Inserted white-label settings definition")

    async def apply(self, user_id: str, db: AsyncSession, **kwargs) -> bool:
        try:
            await self._ensure_definition(db)

            check_stmt = text(
                "SELECT id FROM settings_instances WHERE definition_id = :def_id AND user_id = :uid"
            )
            res = await db.execute(
                check_stmt, {"def_id": self.WHITE_LABEL_ID, "uid": user_id}
            )
            if res.scalar_one_or_none():
                logger.info("White-label settings already present for user %s", user_id)
                return True

            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            insert_stmt = text(
                """
                INSERT INTO settings_instances
                (id, definition_id, name, value, scope, user_id, page_id, created_at, updated_at)
                VALUES
                (:id, :definition_id, :name, :value, :scope, :user_id, :page_id, :created_at, :updated_at)
                """
            )
            await db.execute(
                insert_stmt,
                {
                    "id": str(uuid4()).replace("-", ""),
                    "definition_id": self.WHITE_LABEL_ID,
                    "name": self.WHITE_LABEL_NAME,
                    "value": json.dumps(self.WHITE_LABEL_DEFAULT),
                    "scope": "user",
                    "user_id": user_id,
                    "page_id": None,
                    "created_at": current_time,
                    "updated_at": current_time,
                },
            )
            logger.info("Inserted white-label settings for user %s", user_id)
            return True
        except Exception as e:
            logger.error("Error applying SettingsToV065 updater for %s: %s", user_id, e)
            return False


register_updater(SettingsToV065)
