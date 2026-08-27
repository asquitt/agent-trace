"""Add the durable observability scheduler lease.

Revision ID: 008
Revises: 007
Create Date: 2026-08-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "observability_scheduler_leases",
        sa.Column("lease_name", sa.String(length=120), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("fence_token", sa.BigInteger(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("lease_name"),
    )
    op.create_index(
        "ix_observability_scheduler_leases_lease_expires_at",
        "observability_scheduler_leases",
        ["lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_observability_scheduler_leases_lease_expires_at",
        table_name="observability_scheduler_leases",
    )
    op.drop_table("observability_scheduler_leases")
