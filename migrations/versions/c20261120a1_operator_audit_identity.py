"""Add restricted OIDC operator identity to audit rows."""

import sqlalchemy as sa
from alembic import op

revision = "c20261120a1"
down_revision = "b3636dd7ffff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_records", sa.Column("actor_issuer", sa.String(length=500), nullable=True))
    op.add_column("audit_records", sa.Column("actor_subject", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_records", "actor_subject")
    op.drop_column("audit_records", "actor_issuer")
