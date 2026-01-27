"""Add backend plugin fields

Revision ID: add_backend_plugin_fields
Revises: a1b2c3d4e5f6
Create Date: 2026-01-26

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'add_backend_plugin_fields'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None

def upgrade():
    """Add columns for backend plugin support."""

    # Get database connection and inspector
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check if plugin table exists and get its columns
    if 'plugin' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('plugin')]

        # Add columns only if they don't exist
        if 'endpoints_file' not in columns:
            op.add_column('plugin', sa.Column('endpoints_file', sa.String(), nullable=True,
                                              comment='Backend endpoint file name, e.g., "endpoints.py"'))
        if 'route_prefix' not in columns:
            op.add_column('plugin', sa.Column('route_prefix', sa.String(), nullable=True,
                                              comment='Route prefix for backend plugin, e.g., "/library"'))
        if 'backend_dependencies' not in columns:
            op.add_column('plugin', sa.Column('backend_dependencies', sa.Text(), nullable=True,
                                              comment='JSON list of required backend plugin slugs'))

def downgrade():
    """Remove the backend plugin columns."""

    # Get database connection and inspector
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check if plugin table exists and get its columns
    if 'plugin' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('plugin')]

        # Drop columns only if they exist
        if 'backend_dependencies' in columns:
            op.drop_column('plugin', 'backend_dependencies')
        if 'route_prefix' in columns:
            op.drop_column('plugin', 'route_prefix')
        if 'endpoints_file' in columns:
            op.drop_column('plugin', 'endpoints_file')
