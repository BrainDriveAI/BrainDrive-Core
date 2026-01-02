"""add_service_runtime_fields

Revision ID: 3f2a1a8c7b9d
Revises: d2c74a2d3ab1
Create Date: 2025-12-19 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3f2a1a8c7b9d"
down_revision: Union[str, None] = "d2c74a2d3ab1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("plugin_service_runtime", schema=None) as batch_op:
        batch_op.add_column(sa.Column("stop_command", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("restart_command", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("runtime_dir_key", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("env_inherit", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("env_overrides", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("plugin_service_runtime", schema=None) as batch_op:
        batch_op.drop_column("env_overrides")
        batch_op.drop_column("env_inherit")
        batch_op.drop_column("runtime_dir_key")
        batch_op.drop_column("restart_command")
        batch_op.drop_column("stop_command")
