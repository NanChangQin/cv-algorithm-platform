"""Model versions published from training runs: weights directory and lineage."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261023_0007"
down_revision = "20261023_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("algorithm_versions", sa.Column("model_directory", sa.String(160)))
    op.add_column(
        "algorithm_versions", sa.Column("lineage", postgresql.JSONB(astext_type=sa.Text()))
    )


def downgrade() -> None:
    op.drop_column("algorithm_versions", "lineage")
    op.drop_column("algorithm_versions", "model_directory")
