"""add job system tables

Revision ID: d2c74a2d3ab1
Revises: fa1cc8d52824
Create Date: 2025-03-11 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d2c74a2d3ab1"
down_revision: Union[str, None] = "fa1cc8d52824"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create core job orchestration tables."""
    op.create_table(
        "job_type_definitions",
        sa.Column("job_type", sa.String(length=50), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("handler_class", sa.String(length=200), nullable=False),
        sa.Column("default_config", sa.JSON(), nullable=True),
        sa.Column("payload_schema", sa.JSON(), nullable=True),
        sa.Column("required_permissions", sa.JSON(), nullable=True),
        sa.Column("sandbox_profile", sa.String(length=50), nullable=True),
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

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column(
            "scheduled_for",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_stage", sa.String(length=100), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=True),
        sa.Column("parent_job_id", sa.String(length=36), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["job_type"],
            ["job_type_definitions.job_type"],
            name="fk_jobs_job_type",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_job_id"],
            ["jobs.id"],
            name="fk_jobs_parent_job_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_jobs_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["tenants.id"],
            name="fk_jobs_workspace_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("idempotency_key", "user_id", name="uq_jobs_idempotency_user"),
    )
    op.create_index("idx_jobs_status_priority", "jobs", ["status", "priority", "created_at"])
    op.create_index("idx_jobs_user_status", "jobs", ["user_id", "status", "created_at"])
    op.create_index("idx_jobs_type_status", "jobs", ["job_type", "status", "scheduled_for"])
    op.create_index("idx_jobs_scheduled", "jobs", ["status", "scheduled_for"])
    op.create_index("idx_jobs_cleanup", "jobs", ["expires_at"])

    op.create_table(
        "job_attempts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_job_attempts_job_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_job_attempt_number"),
    )
    op.create_index("idx_job_attempt_job", "job_attempts", ["job_id"])

    op.create_table(
        "job_progress_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_job_progress_events_job_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("job_id", "sequence_number", name="uq_job_progress_sequence"),
    )
    op.create_index(
        "idx_job_progress_events_job_sequence",
        "job_progress_events",
        ["job_id", "sequence_number"],
    )
    op.create_index("idx_job_progress_events_timestamp", "job_progress_events", ["timestamp"])

    op.create_table(
        "job_dependencies",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("depends_on_job_id", sa.String(length=36), nullable=False),
        sa.Column("dependency_type", sa.String(length=20), nullable=False, server_default="success"),
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
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_job_dependencies_job_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["depends_on_job_id"],
            ["jobs.id"],
            name="fk_job_dependencies_depends_on_job_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("job_id", "depends_on_job_id", name="uq_job_dependency"),
    )
    op.create_index("idx_job_deps_depends_on", "job_dependencies", ["depends_on_job_id"])
    op.create_index("idx_job_deps_job", "job_dependencies", ["job_id"])

    op.create_table(
        "job_subscriptions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("subscriber_id", sa.String(length=100), nullable=False),
        sa.Column("subscription_type", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_job_subscriptions_job_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "job_id",
            "subscriber_id",
            "subscription_type",
            name="uq_job_subscription",
        ),
    )
    op.create_index("idx_job_subscriptions_job", "job_subscriptions", ["job_id"])

    op.create_table(
        "worker_capabilities",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("worker_id", sa.String(length=100), nullable=False),
        sa.Column("job_type", sa.String(length=50), nullable=False),
        sa.Column("max_concurrent", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sandbox_profiles", sa.JSON(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
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
        sa.ForeignKeyConstraint(
            ["job_type"],
            ["job_type_definitions.job_type"],
            name="fk_worker_capabilities_job_type",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("worker_id", "job_type", name="uq_worker_job_type"),
    )
    op.create_index("idx_worker_capabilities_job_type", "worker_capabilities", ["job_type"])


def downgrade() -> None:
    """Drop job orchestration tables."""
    op.drop_index("idx_worker_capabilities_job_type", table_name="worker_capabilities")
    op.drop_table("worker_capabilities")

    op.drop_index("idx_job_subscriptions_job", table_name="job_subscriptions")
    op.drop_table("job_subscriptions")

    op.drop_index("idx_job_deps_job", table_name="job_dependencies")
    op.drop_index("idx_job_deps_depends_on", table_name="job_dependencies")
    op.drop_table("job_dependencies")

    op.drop_index("idx_job_progress_events_timestamp", table_name="job_progress_events")
    op.drop_index(
        "idx_job_progress_events_job_sequence",
        table_name="job_progress_events",
    )
    op.drop_table("job_progress_events")

    op.drop_index("idx_job_attempt_job", table_name="job_attempts")
    op.drop_table("job_attempts")

    op.drop_index("idx_jobs_cleanup", table_name="jobs")
    op.drop_index("idx_jobs_scheduled", table_name="jobs")
    op.drop_index("idx_jobs_type_status", table_name="jobs")
    op.drop_index("idx_jobs_user_status", table_name="jobs")
    op.drop_index("idx_jobs_status_priority", table_name="jobs")
    op.drop_table("jobs")

    op.drop_table("job_type_definitions")
