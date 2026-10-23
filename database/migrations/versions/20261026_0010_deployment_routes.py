"""Deployment service routes, readiness-gated rollout, draining, and rollback."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261026_0010"
down_revision = "20261025_0009"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.drop_constraint("ck_deployments_status", "deployments", type_="check")
    op.create_check_constraint(
        "ck_deployments_status",
        "deployments",
        "status IN ('queued', 'preparing', 'running', 'draining', 'stopping', 'stopped', 'failed')",
    )
    op.create_table(
        "deployment_routes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("active_deployment_id", UUID, nullable=False),
        sa.Column("previous_deployment_id", UUID),
        sa.Column("candidate_deployment_id", UUID),
        sa.Column("drain_timeout_seconds", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("last_error", sa.Text()),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["active_deployment_id"], ["deployments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["previous_deployment_id"], ["deployments.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_deployment_id"], ["deployments.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("project_id", "key", name="uq_deployment_routes_project_key"),
        sa.CheckConstraint(
            "drain_timeout_seconds >= 5 AND drain_timeout_seconds <= 3600",
            name="ck_deployment_routes_drain_timeout",
        ),
    )
    op.create_index("ix_deployment_routes_project", "deployment_routes", ["project_id"])
    op.create_index("ix_deployment_routes_active", "deployment_routes", ["active_deployment_id"])
    op.create_index(
        "ix_deployment_routes_candidate", "deployment_routes", ["candidate_deployment_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_deployment_routes_candidate", table_name="deployment_routes")
    op.drop_index("ix_deployment_routes_active", table_name="deployment_routes")
    op.drop_index("ix_deployment_routes_project", table_name="deployment_routes")
    op.drop_table("deployment_routes")
    op.execute("UPDATE deployments SET status = 'stopping' WHERE status = 'draining'")
    op.drop_constraint("ck_deployments_status", "deployments", type_="check")
    op.create_check_constraint(
        "ck_deployments_status",
        "deployments",
        "status IN ('queued', 'preparing', 'running', 'stopping', 'stopped', 'failed')",
    )
