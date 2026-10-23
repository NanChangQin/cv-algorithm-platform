"""Deployment runtime profiles, verification evidence and exact task binding."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261025_0009"
down_revision = "20261024_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deployments", sa.Column("runtime_profile", postgresql.JSONB()))
    op.add_column("deployments", sa.Column("readiness", postgresql.JSONB()))
    # Preserve binding after deletion. A missing deployment must fail, never reschedule.
    op.add_column("inference_tasks", sa.Column("deployment_id", postgresql.UUID(as_uuid=True)))
    op.create_index("ix_tasks_deployment", "inference_tasks", ["deployment_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_deployment", table_name="inference_tasks")
    op.drop_column("inference_tasks", "deployment_id")
    op.drop_column("deployments", "readiness")
    op.drop_column("deployments", "runtime_profile")
