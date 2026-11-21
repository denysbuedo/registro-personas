"""Store provenance for each source reference."""

import sqlalchemy as sa
from alembic import op

revision = "c20261120a2"
down_revision = "c20261120a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source_references", sa.Column("evidence_ref", sa.Uuid(), nullable=True))
    op.add_column(
        "source_references", sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        sa.text(
            """
            UPDATE source_references AS source_ref
            SET evidence_ref = person.name_evidence_ref,
                observed_at = person.name_observed_at
            FROM persons AS person
            WHERE source_ref.person_id = person.person_id
              AND source_ref.system = person.name_source_system
            """
        )
    )


def downgrade() -> None:
    op.drop_column("source_references", "observed_at")
    op.drop_column("source_references", "evidence_ref")
