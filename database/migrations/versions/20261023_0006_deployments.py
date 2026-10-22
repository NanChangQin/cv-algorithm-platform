"""Targeted deployments of algorithm versions to chosen servers and GPUs."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261023_0006"
down_revision = "20261023_0005"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "deployments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("algorithm_version_id", UUID, nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("gpu_device_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("steps", JSONB, nullable=False, server_default="[]"),
        sa.Column("instance_id", sa.String(128)),
        sa.Column("error", sa.Text()),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", TIMESTAMP),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["algorithm_version_id"], ["algorithm_versions.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'preparing', 'running', 'stopping', 'stopped', 'failed')",
            name="ck_deployments_status",
        ),
    )
    op.create_index("ix_deployments_project_created", "deployments", ["project_id", "created_at"])
    op.create_index("ix_deployments_version", "deployments", ["algorithm_version_id"])
    op.create_index("ix_deployments_status", "deployments", ["status"])


def downgrade() -> None:
    op.drop_index("ix_deployments_status", table_name="deployments")
    op.drop_index("ix_deployments_version", table_name="deployments")
    op.drop_index("ix_deployments_project_created", table_name="deployments")
    op.drop_table("deployments")
