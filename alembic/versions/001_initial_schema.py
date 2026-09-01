"""Migracion inicial: inspections, websocket_events, users.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-08-31

Refleja app/models/*.py (fuente de verdad).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inspections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("serial_number", sa.String(length=50), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("date_folder", sa.String(length=10), nullable=False),
        sa.Column("month_folder", sa.String(length=20), nullable=False),
        sa.Column("station_id", sa.String(length=50), nullable=False),
        sa.Column("operator_id", sa.String(length=50), nullable=True),
        sa.Column("socket_type_hint", sa.String(length=50), nullable=True),
        sa.Column("socket_type_detected", sa.String(length=50), nullable=True),
        sa.Column("ai_status", sa.String(length=20), nullable=False),
        sa.Column("ai_defect_count", sa.Integer(), nullable=True),
        sa.Column(
            "ai_defect_locations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("ai_confidence", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("ai_observations", sa.Text(), nullable=True),
        sa.Column("ai_model_used", sa.String(length=100), nullable=True),
        sa.Column("ai_inference_time_ms", sa.Integer(), nullable=True),
        sa.Column("ai_raw_response", sa.Text(), nullable=True),
        sa.Column("final_disposition", sa.String(length=20), nullable=False),
        sa.Column("manual_override", sa.Boolean(), nullable=True),
        sa.Column("manual_override_reason", sa.Text(), nullable=True),
        sa.Column("evidence_folder_path", sa.Text(), nullable=False),
        sa.Column(
            "original_image_name",
            sa.String(length=100),
            server_default="01_original.png",
            nullable=True,
        ),
        sa.Column(
            "annotated_image_name",
            sa.String(length=100),
            server_default="02_annotated.png",
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inspections_serial_number", "inspections", ["serial_number"])
    op.create_index("ix_inspections_timestamp", "inspections", ["timestamp"])
    op.create_index("ix_inspections_date_folder", "inspections", ["date_folder"])
    op.create_index("ix_inspections_station_id", "inspections", ["station_id"])
    op.create_index("ix_inspections_ai_status", "inspections", ["ai_status"])

    op.create_table(
        "websocket_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("station_id", sa.String(length=50), nullable=True),
        sa.Column(
            "broadcasted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_websocket_events_event_type", "websocket_events", ["event_type"]
    )
    op.create_index(
        "ix_websocket_events_station_id", "websocket_events", ["station_id"]
    )
    op.create_index(
        "ix_websocket_events_broadcasted_at",
        "websocket_events",
        ["broadcasted_at"],
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
    op.drop_index(
        "ix_websocket_events_broadcasted_at", table_name="websocket_events"
    )
    op.drop_index("ix_websocket_events_station_id", table_name="websocket_events")
    op.drop_index("ix_websocket_events_event_type", table_name="websocket_events")
    op.drop_table("websocket_events")
    op.drop_index("ix_inspections_ai_status", table_name="inspections")
    op.drop_index("ix_inspections_station_id", table_name="inspections")
    op.drop_index("ix_inspections_date_folder", table_name="inspections")
    op.drop_index("ix_inspections_timestamp", table_name="inspections")
    op.drop_index("ix_inspections_serial_number", table_name="inspections")
    op.drop_table("inspections")
