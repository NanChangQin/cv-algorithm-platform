"""Training datasets and training runs."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261023_0005"
down_revision = "20261022_0004"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "training_datasets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("format", sa.String(32), nullable=False),
        sa.Column("class_names", JSONB, nullable=False),
        sa.Column("stats", JSONB, nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_training_datasets_project_created", "training_datasets", ["project_id", "created_at"]
    )
    op.create_table(
        "training_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("dataset_id", UUID, nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("parameters", JSONB, nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("gpu_device_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(200)),
        sa.Column("progress", JSONB),
        sa.Column("metrics", JSONB, nullable=False, server_default="[]"),
        sa.Column("artifacts", JSONB, nullable=False, server_default="{}"),
        sa.Column("error", sa.Text()),
        sa.Column("logs", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", TIMESTAMP),
        sa.Column("finished_at", TIMESTAMP),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        # 有训练记录的数据集不能删除，训练记录要能追溯到数据集
        sa.ForeignKeyConstraint(["dataset_id"], ["training_datasets.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "status IN ('queued', 'preparing', 'running', 'stopping', 'completed', 'stopped',"
            " 'failed')",
            name="ck_training_runs_status",
        ),
    )
    op.create_index(
        "ix_training_runs_project_created", "training_runs", ["project_id", "created_at"]
    )
    op.create_index("ix_training_runs_status", "training_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_training_runs_status", table_name="training_runs")
    op.drop_index("ix_training_runs_project_created", table_name="training_runs")
    op.drop_table("training_runs")
    op.drop_index("ix_training_datasets_project_created", table_name="training_datasets")
    op.drop_table("training_datasets")
