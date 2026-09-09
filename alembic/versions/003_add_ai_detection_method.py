"""Agrega columna ai_detection_method a la tabla inspections.

Revision ID: 003_add_ai_detection_method
Revises: 002_add_ai_pin_count
Create Date: 2026-09-04

"""
import sqlalchemy as sa
from alembic import op

revision = "003_add_ai_detection_method"
down_revision = "002_add_ai_pin_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inspections",
        sa.Column("ai_detection_method", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("inspections", "ai_detection_method")
