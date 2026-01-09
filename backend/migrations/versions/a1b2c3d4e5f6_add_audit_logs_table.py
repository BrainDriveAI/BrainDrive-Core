"""add_audit_logs_table

Revision ID: a1b2c3d4e5f6
Revises: 3f2a1a8c7b9d
Create Date: 2026-01-09 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "3f2a1a8c7b9d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the audit_logs table for security event tracking."""
    op.create_table(
        "audit_logs",
        # Primary key
        sa.Column("id", sa.String(length=36), primary_key=True),
        
        # Event identification
        sa.Column("event_type", sa.String(length=100), nullable=False),
        
        # Actor information
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.String(length=100), nullable=True),
        
        # Request context
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("method", sa.String(length=10), nullable=True),
        sa.Column("path", sa.String(length=500), nullable=True),
        
        # Resource affected
        sa.Column("resource_type", sa.String(length=50), nullable=True),
        sa.Column("resource_id", sa.String(length=100), nullable=True),
        
        # Event result
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        
        # Additional metadata (JSON)
        sa.Column("extra_data", sa.JSON(), nullable=True),
        
        # Timestamp
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        
        # Standard timestamp columns
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    
    # Create indexes for efficient querying
    op.create_index("idx_audit_event_type", "audit_logs", ["event_type"])
    op.create_index("idx_audit_actor", "audit_logs", ["actor_type", "actor_id"])
    op.create_index("idx_audit_timestamp", "audit_logs", ["timestamp"])
    op.create_index("idx_audit_request_id", "audit_logs", ["request_id"])
    op.create_index("idx_audit_resource", "audit_logs", ["resource_type", "resource_id"])
    op.create_index("idx_audit_status", "audit_logs", ["status"])
    op.create_index("idx_audit_type_time", "audit_logs", ["event_type", "timestamp"])


def downgrade() -> None:
    """Drop the audit_logs table and its indexes."""
    # Drop indexes first
    op.drop_index("idx_audit_type_time", table_name="audit_logs")
    op.drop_index("idx_audit_status", table_name="audit_logs")
    op.drop_index("idx_audit_resource", table_name="audit_logs")
    op.drop_index("idx_audit_request_id", table_name="audit_logs")
    op.drop_index("idx_audit_timestamp", table_name="audit_logs")
    op.drop_index("idx_audit_actor", table_name="audit_logs")
    op.drop_index("idx_audit_event_type", table_name="audit_logs")
    
    # Drop table
    op.drop_table("audit_logs")
