"""add mcp registry tables

Revision ID: 2cb3f0bb9d9d
Revises: 1f4b3a9d7c2e
Create Date: 2026-02-10 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2cb3f0bb9d9d"
down_revision: Union[str, None] = "1f4b3a9d7c2e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return name in inspector.get_table_names()


def _index_exists(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    indexes = inspector.get_indexes(table_name) if table_name in inspector.get_table_names() else []
    return any(index.get("name") == index_name for index in indexes)


def upgrade() -> None:
    if not _table_exists("mcp_server_registry"):
        op.create_table(
            "mcp_server_registry",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("user_id", sa.String(length=32), nullable=False),
            sa.Column("plugin_slug", sa.String(), nullable=True),
            sa.Column("runtime_id", sa.String(), nullable=True),
            sa.Column("base_url", sa.String(), nullable=False),
            sa.Column("tools_url", sa.String(), nullable=False),
            sa.Column("healthcheck_url", sa.String(), nullable=True),
            sa.Column("tool_call_url_template", sa.String(), nullable=False, server_default="/tool:{name}"),
            sa.Column("auth_mode", sa.String(), nullable=False, server_default="none"),
            sa.Column("auth_ref", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="registered"),
            sa.Column("last_sync_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["runtime_id"], ["plugin_service_runtime.id"], name="fk_mcp_server_registry_runtime_id"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_mcp_server_registry_user_id"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "runtime_id", name="uq_mcp_server_registry_user_runtime"),
        )

    if not _index_exists("mcp_server_registry", "ix_mcp_server_registry_user_id"):
        op.create_index("ix_mcp_server_registry_user_id", "mcp_server_registry", ["user_id"], unique=False)
    if not _index_exists("mcp_server_registry", "ix_mcp_server_registry_plugin_slug"):
        op.create_index("ix_mcp_server_registry_plugin_slug", "mcp_server_registry", ["plugin_slug"], unique=False)
    if not _index_exists("mcp_server_registry", "ix_mcp_server_registry_runtime_id"):
        op.create_index("ix_mcp_server_registry_runtime_id", "mcp_server_registry", ["runtime_id"], unique=False)

    if not _table_exists("mcp_tool_registry"):
        op.create_table(
            "mcp_tool_registry",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("server_id", sa.String(length=32), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("schema_json", sa.Text(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("source_hash", sa.String(length=64), nullable=False),
            sa.Column("version", sa.String(), nullable=True),
            sa.Column("safety_class", sa.String(), nullable=False, server_default="read_only"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["server_id"], ["mcp_server_registry.id"], name="fk_mcp_tool_registry_server_id"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("server_id", "name", name="uq_mcp_tool_registry_server_tool"),
        )

    if not _index_exists("mcp_tool_registry", "ix_mcp_tool_registry_server_id"):
        op.create_index("ix_mcp_tool_registry_server_id", "mcp_tool_registry", ["server_id"], unique=False)


def downgrade() -> None:
    if _table_exists("mcp_tool_registry"):
        if _index_exists("mcp_tool_registry", "ix_mcp_tool_registry_server_id"):
            op.drop_index("ix_mcp_tool_registry_server_id", table_name="mcp_tool_registry")
        op.drop_table("mcp_tool_registry")

    if _table_exists("mcp_server_registry"):
        if _index_exists("mcp_server_registry", "ix_mcp_server_registry_runtime_id"):
            op.drop_index("ix_mcp_server_registry_runtime_id", table_name="mcp_server_registry")
        if _index_exists("mcp_server_registry", "ix_mcp_server_registry_plugin_slug"):
            op.drop_index("ix_mcp_server_registry_plugin_slug", table_name="mcp_server_registry")
        if _index_exists("mcp_server_registry", "ix_mcp_server_registry_user_id"):
            op.drop_index("ix_mcp_server_registry_user_id", table_name="mcp_server_registry")
        op.drop_table("mcp_server_registry")
