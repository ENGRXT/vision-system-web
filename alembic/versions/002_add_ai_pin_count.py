"""Agrega columna ai_pin_count a la tabla inspections.

Revision ID: 002_add_ai_pin_count
Revises: 001_initial_schema
Create Date: 2026-09-02

"""
import sqlalchemy as sa
from alembic import op

revision = "002_add_ai_pin_count"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inspections",
        sa.Column("ai_pin_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("inspections", "ai_pin_count")
