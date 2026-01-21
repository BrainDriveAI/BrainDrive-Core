"""Add backend plugin fields

Revision ID: add_backend_plugin_fields
Revises: a1b2c3d4e5f6
Create Date: 2026-01-21

Adds fields to support backend plugin architecture:
- plugin_type: "frontend", "backend", or "fullstack"
- endpoints_file: Python file containing plugin endpoints (e.g., "endpoints.py")
- route_prefix: URL prefix for plugin routes (e.g., "/library")
- backend_dependencies: JSON list of required backend plugin slugs
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "add_backend_plugin_fields"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add backend plugin fields to the plugin table."""
    # Get database connection and inspector
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check if plugin table exists and get its columns
    if "plugin" in inspector.get_table_names():
        columns = [col["name"] for col in inspector.get_columns("plugin")]

        # Add columns only if they don't exist
        if "plugin_type" not in columns:
            op.add_column(
                "plugin",
                sa.Column(
                    "plugin_type",
                    sa.String(20),
                    nullable=False,
                    server_default="frontend",
                    comment="Plugin type: frontend, backend, or fullstack",
                ),
            )

        if "endpoints_file" not in columns:
            op.add_column(
                "plugin",
                sa.Column(
                    "endpoints_file",
                    sa.String(),
                    nullable=True,
                    comment="Python file containing plugin endpoints (e.g., endpoints.py)",
                ),
            )

        if "route_prefix" not in columns:
            op.add_column(
                "plugin",
                sa.Column(
                    "route_prefix",
                    sa.String(),
                    nullable=True,
                    comment="URL prefix for plugin routes (e.g., /library)",
                ),
            )

        if "backend_dependencies" not in columns:
            op.add_column(
                "plugin",
                sa.Column(
                    "backend_dependencies",
                    sa.Text(),
                    nullable=True,
                    comment="JSON list of required backend plugin slugs",
                ),
            )


def downgrade() -> None:
    """Remove backend plugin fields from the plugin table."""
    # Get database connection and inspector
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check if plugin table exists and get its columns
    if "plugin" in inspector.get_table_names():
        columns = [col["name"] for col in inspector.get_columns("plugin")]

        # Drop columns only if they exist
        if "backend_dependencies" in columns:
            op.drop_column("plugin", "backend_dependencies")
        if "route_prefix" in columns:
            op.drop_column("plugin", "route_prefix")
        if "endpoints_file" in columns:
            op.drop_column("plugin", "endpoints_file")
        if "plugin_type" in columns:
            op.drop_column("plugin", "plugin_type")
