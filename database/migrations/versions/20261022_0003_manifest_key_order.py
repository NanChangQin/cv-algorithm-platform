"""Store algorithm manifests as json so parameter order survives a round trip.

JSONB 会按键长度重排对象，参数表单的顺序因此和 manifest 里写的不一致。
已存的数据顺序已经丢失：演示算法启动时会按代码里的定义刷新，导入的算法需要重新导入。
"""

from alembic import op

revision = "20261022_0003"
down_revision = "20261016_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE algorithm_versions ALTER COLUMN manifest TYPE json USING manifest::json"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE algorithm_versions ALTER COLUMN manifest TYPE jsonb USING manifest::jsonb"
    )
