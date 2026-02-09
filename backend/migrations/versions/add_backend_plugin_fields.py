"""add backend plugin endpoint fields

Revision ID: 1f4b3a9d7c2e
Revises: a1b2c3d4e5f6
Create Date: 2026-02-09 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "1f4b3a9d7c2e"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _plugin_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns("plugin")}


def upgrade() -> None:
    existing = _plugin_columns()

    with op.batch_alter_table("plugin", schema=None) as batch_op:
        if "plugin_type" not in existing:
            batch_op.add_column(sa.Column("plugin_type", sa.String(), nullable=True))
        if "endpoints_file" not in existing:
            batch_op.add_column(sa.Column("endpoints_file", sa.String(), nullable=True))
        if "route_prefix" not in existing:
            batch_op.add_column(sa.Column("route_prefix", sa.String(), nullable=True))
        if "backend_dependencies" not in existing:
            batch_op.add_column(sa.Column("backend_dependencies", sa.Text(), nullable=True))


def downgrade() -> None:
    existing = _plugin_columns()

    with op.batch_alter_table("plugin", schema=None) as batch_op:
        if "backend_dependencies" in existing:
            batch_op.drop_column("backend_dependencies")
        if "route_prefix" in existing:
            batch_op.drop_column("route_prefix")
        if "endpoints_file" in existing:
            batch_op.drop_column("endpoints_file")
        if "plugin_type" in existing:
            batch_op.drop_column("plugin_type")

