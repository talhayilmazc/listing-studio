"""A template's "This template has several garments in one photo" switch (off by default).

``mockup_template.several_garments``: only when it is on may the analysis suggest more than
one print area per photo; otherwise each photo gets its main garment's area and the seller
adds more with "Add area". Nothing existing changes here: existing unconfirmed photos with
several suggested areas are reduced by ``python -m app.cli mockup-single-area --apply``,
which also switches the setting on for templates where the seller confirmed several areas.

Revision ID: 0071_mockup_several_garments
Revises: 0070_mockup_placement
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0071_mockup_several_garments"
down_revision: str | None = "0070_mockup_placement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mockup_template", sa.Column("several_garments", sa.Boolean(), nullable=False,
                                               server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("mockup_template", "several_garments")
