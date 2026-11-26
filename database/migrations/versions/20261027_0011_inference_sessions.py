"""Persistent inference sessions and paged frame results."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261027_0011"
down_revision = "20261026_0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inference_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_inference_sessions_project_created",
        "inference_sessions",
        ["project_id", "created_at"],
    )
    op.create_table(
        "inference_frames",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("frame_index", sa.Integer(), primary_key=True),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["inference_sessions.id"], ondelete="CASCADE"
        ),
    )


def downgrade():
    op.drop_table("inference_frames")
    op.drop_table("inference_sessions")
