"""Immutable dataset versions, import provenance and reproducible splits."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261024_0008"
down_revision = "20261023_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "training_dataset_series",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("project_id", uuid, nullable=False),
        sa.Column("next_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.add_column("training_datasets", sa.Column("family_id", uuid))
    op.add_column("training_datasets", sa.Column("version", sa.Integer(),
                                              nullable=False, server_default="1"))
    op.add_column("training_datasets", sa.Column("parent_id", uuid))
    op.add_column("training_datasets", sa.Column("source_sha256", sa.String(64)))
    op.add_column("training_datasets", sa.Column("split_config", postgresql.JSONB(),
                                              nullable=False, server_default="{}"))
    op.execute("INSERT INTO training_dataset_series (id, project_id, next_version) "
               "SELECT id, project_id, 2 FROM training_datasets")
    op.execute("UPDATE training_datasets SET family_id = id")
    op.alter_column("training_datasets", "family_id", nullable=False)
    op.create_foreign_key("fk_dataset_series", "training_datasets", "training_dataset_series",
                          ["family_id"], ["id"])
    op.create_foreign_key("fk_dataset_parent", "training_datasets", "training_datasets",
                          ["parent_id"], ["id"], ondelete="RESTRICT")
    op.create_unique_constraint("uq_dataset_family_version", "training_datasets",
                                ["family_id", "version"])
    op.create_check_constraint("ck_dataset_version", "training_datasets", "version > 0")


def downgrade() -> None:
    op.drop_constraint("ck_dataset_version", "training_datasets", type_="check")
    op.drop_constraint("uq_dataset_family_version", "training_datasets", type_="unique")
    op.drop_constraint("fk_dataset_parent", "training_datasets", type_="foreignkey")
    op.drop_constraint("fk_dataset_series", "training_datasets", type_="foreignkey")
    for name in ("split_config", "source_sha256", "parent_id", "version", "family_id"):
        op.drop_column("training_datasets", name)
    op.drop_table("training_dataset_series")
