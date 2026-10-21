"""Store evaluation datasets with ground truth and their evaluation runs."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261022_0004"
down_revision = "20261022_0003"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "evaluation_datasets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("task_type", sa.String(32), nullable=False),
        sa.Column("items", JSONB, nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_evaluation_datasets_project_created", "evaluation_datasets", ["project_id", "created_at"]
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("dataset_id", UUID, nullable=False),
        sa.Column("algorithm_version_ids", JSONB, nullable=False),
        sa.Column("task_ids", JSONB, nullable=False),
        sa.Column("report", JSONB),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["evaluation_datasets.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_evaluation_runs_project_created", "evaluation_runs", ["project_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_runs_project_created", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
    op.drop_index("ix_evaluation_datasets_project_created", table_name="evaluation_datasets")
    op.drop_table("evaluation_datasets")
