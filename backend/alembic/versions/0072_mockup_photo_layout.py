"""Each template photo has its own layout; designs may have a back file.

* ``mockup_photo.layout``: "single" (default), "front_back" or "several"; who set it in
  ``layout_source`` ("auto": the analysis, when sure; "seller"). It replaces the template's
  "several garments in one photo" switch, because one template mixes single, front & back and
  several-garment photos: photos of templates whose switch is on get "several" (set by the
  seller). ``mockup_template.several_garments`` stays in the table, unread.
* ``mockup_template.back_without_design``: what a back area prints when a design has no back
  file: "front" (the front design, default) or "plain" (nothing).
* ``mockup_design.back_key``: the design's optional back file.

Nothing is deleted; confirmed areas do not change.

Revision ID: 0072_mockup_photo_layout
Revises: 0071_mockup_several_garments
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0072_mockup_photo_layout"
down_revision: str | None = "0071_mockup_several_garments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mockup_photo", sa.Column("layout", sa.Text(), nullable=False, server_default="single"))
    op.add_column("mockup_photo", sa.Column("layout_source", sa.Text(), nullable=False, server_default="auto"))
    op.add_column("mockup_template", sa.Column("back_without_design", sa.Text(), nullable=False, server_default="front"))
    op.add_column("mockup_design", sa.Column("back_key", sa.Text(), nullable=True))
    photo = sa.table("mockup_photo", sa.column("template_id", sa.Uuid()), sa.column("layout", sa.Text()),
                     sa.column("layout_source", sa.Text()))
    template = sa.table("mockup_template", sa.column("id", sa.Uuid()), sa.column("several_garments", sa.Boolean()))
    switched = sa.select(template.c.id).where(template.c.several_garments.is_(True))
    op.execute(photo.update().where(photo.c.template_id.in_(switched)).values(layout="several", layout_source="seller"))


def downgrade() -> None:
    op.drop_column("mockup_design", "back_key")
    op.drop_column("mockup_template", "back_without_design")
    op.drop_column("mockup_photo", "layout_source")
    op.drop_column("mockup_photo", "layout")
