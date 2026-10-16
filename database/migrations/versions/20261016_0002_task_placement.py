"""Persist requested and actual task execution placement."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261016_0002"
down_revision = "20260813_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inference_tasks", sa.Column("node_id", sa.String(64), nullable=True))
    op.add_column(
        "inference_tasks",
        sa.Column("gpu_device_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "inference_tasks", sa.Column("execution_node_id", sa.String(64), nullable=True)
    )
    op.add_column(
        "inference_tasks",
        sa.Column(
            "execution_gpu_device_ids", postgresql.JSONB(), nullable=False, server_default="[]"
        ),
    )


def downgrade() -> None:
    op.drop_column("inference_tasks", "execution_gpu_device_ids")
    op.drop_column("inference_tasks", "execution_node_id")
    op.drop_column("inference_tasks", "gpu_device_ids")
    op.drop_column("inference_tasks", "node_id")
