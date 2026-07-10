"""Add soft-delete columns to users and workspaces.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("workspaces", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_workspaces_deleted_at", "workspaces", ["deleted_at"])

    op.add_column("users", sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_users_deleted_at", table_name="users")
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "is_deleted")

    op.drop_index("ix_workspaces_deleted_at", table_name="workspaces")
    op.drop_column("workspaces", "deleted_at")
    op.drop_column("workspaces", "is_deleted")
