"""Add mentor availability and capacity columns to profiles.

Revision ID: c3d1e2f4a567
Revises: b8e5f2c4a913
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d1e2f4a567"
down_revision: Union[str, None] = "b8e5f2c4a913"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("profiles") as batch_op:
        batch_op.add_column(
            sa.Column("is_accepting_mentees", sa.Boolean(), server_default=sa.text("1"), nullable=False)
        )
        batch_op.add_column(
            sa.Column("max_mentees", sa.Integer(), server_default=sa.text("3"), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("profiles") as batch_op:
        batch_op.drop_column("max_mentees")
        batch_op.drop_column("is_accepting_mentees")
