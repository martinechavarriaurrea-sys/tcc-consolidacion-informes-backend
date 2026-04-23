"""Add report_files table

Revision ID: 002
Revises: 001
Create Date: 2026-04-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "report_files",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("report_type", sa.String(20), nullable=False),
        sa.Column("format", sa.String(10), nullable=False),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("cycle_label", sa.String(10), nullable=True),
        sa.Column("week_start", sa.Date(), nullable=True),
        sa.Column("week_end", sa.Date(), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("email_sent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("email_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_report_files_type_generated",
        "report_files",
        ["report_type", "generated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_report_files_type_generated", table_name="report_files")
    op.drop_table("report_files")
