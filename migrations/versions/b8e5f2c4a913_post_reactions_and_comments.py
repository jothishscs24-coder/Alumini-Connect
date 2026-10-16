"""Add persisted post reactions and comments.

Revision ID: b8e5f2c4a913
Revises: 87c81889a0a6
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8e5f2c4a913"
down_revision: Union[str, None] = "87c81889a0a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "post_reactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("post_id", "user_id", "kind", name="uq_post_reaction"),
    )
    op.create_index(op.f("ix_post_reactions_post_id"), "post_reactions", ["post_id"], unique=False)
    op.create_index(op.f("ix_post_reactions_user_id"), "post_reactions", ["user_id"], unique=False)
    op.create_table(
        "post_comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_post_comments_post_id"), "post_comments", ["post_id"], unique=False)
    op.create_index(op.f("ix_post_comments_author_id"), "post_comments", ["author_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_post_comments_author_id"), table_name="post_comments")
    op.drop_index(op.f("ix_post_comments_post_id"), table_name="post_comments")
    op.drop_table("post_comments")
    op.drop_index(op.f("ix_post_reactions_user_id"), table_name="post_reactions")
    op.drop_index(op.f("ix_post_reactions_post_id"), table_name="post_reactions")
    op.drop_table("post_reactions")
