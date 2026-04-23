"""Initial schema — todas las tablas del sistema TCC

Revision ID: 001
Revises:
Create Date: 2026-04-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shipments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tracking_number", sa.String(100), nullable=False),
        sa.Column("advisor_name", sa.String(200), nullable=False),
        sa.Column("client_name", sa.String(200), nullable=True),
        sa.Column("package_type", sa.String(100), nullable=True),
        sa.Column("destination", sa.String(300), nullable=True),
        sa.Column("current_status", sa.String(50), nullable=False, server_default="desconocido"),
        sa.Column("current_status_raw", sa.Text(), nullable=True),
        sa.Column("current_status_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shipments_tracking_number", "shipments", ["tracking_number"], unique=True)
    op.create_index("ix_shipments_is_active", "shipments", ["is_active"])
    op.create_index("ix_shipments_current_status", "shipments", ["current_status"])
    op.create_index("ix_shipments_advisor_name", "shipments", ["advisor_name"])

    op.create_table(
        "shipment_tracking_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("shipment_id", sa.Integer(), nullable=False),
        sa.Column("status_normalized", sa.String(50), nullable=False),
        sa.Column("status_raw", sa.Text(), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("payload_snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["shipment_id"], ["shipments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tracking_events_shipment_status", "shipment_tracking_events", ["shipment_id", "status_normalized"])
    op.create_index("ix_tracking_events_event_at", "shipment_tracking_events", ["event_at"])

    op.create_table(
        "tracking_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_type", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("shipments_checked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shipments_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shipments_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "weekly_rollups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("week_end", sa.Date(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("total_shipments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_delivered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_in_transit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_with_issues", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_carried_forward", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("file_path", sa.String(500), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_weekly_rollups_week_start", "weekly_rollups", ["week_start"])

    op.create_table(
        "alert_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("shipment_id", sa.Integer(), nullable=False),
        sa.Column("alert_type", sa.String(50), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["shipment_id"], ["shipments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_events_shipment_id", "alert_events", ["shipment_id"])

    op.create_table(
        "email_recipients",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("report_type", sa.String(50), nullable=False),
        sa.Column("recipient_name", sa.String(200), nullable=False),
        sa.Column("recipient_email", sa.String(254), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_recipients_type_active", "email_recipients", ["report_type", "is_active"])

    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_app_settings_key", "app_settings", ["key"], unique=True)


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("email_recipients")
    op.drop_table("alert_events")
    op.drop_table("weekly_rollups")
    op.drop_table("tracking_runs")
    op.drop_table("shipment_tracking_events")
    op.drop_table("shipments")
