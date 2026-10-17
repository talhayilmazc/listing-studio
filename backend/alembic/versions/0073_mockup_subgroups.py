"""Subgroups inside a template (a template "Comfort Colors" split into "Comfort Colors White" and
"Comfort Colors Black"), designs with a version per subgroup.

* ``mockup_subgroup``: a template's named subgroups, each with "Use on" (``use_on``: "any",
  "dark" garments or "light" garments) and the file-name suffix that pairs a design's files
  ("-white", "-black"). Every existing template gets one default subgroup named after it, and
  its photos belong to it: nothing changes visibly until the seller adds a second.
* ``mockup_photo.subgroup_id``: the subgroup a photo belongs to.
* ``mockup_design.subgroup``: the subgroup (by name, so it serves every template's subgroup of
  that name) its file is for; NULL = every subgroup, as before. ``pair_base``: its file name
  without the subgroup's suffix (how the other versions find it). ``ink``: "light", "dark" or
  "mixed", read from its opaque pixels (suggests a subgroup for a file that does not pair).
* ``mockup_design_version``: a design's file for another subgroup (one listing per design;
  each subgroup's mockups use its own version).

Nothing is deleted.

Revision ID: 0073_mockup_subgroups
Revises: 0072_mockup_photo_layout
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from alembic import op

revision: str = "0073_mockup_subgroups"
down_revision: str | None = "0072_mockup_photo_layout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mockup_subgroup",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_id", sa.Uuid(), sa.ForeignKey("mockup_template.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("use_on", sa.Text(), nullable=False, server_default="any"),
        sa.Column("suffix", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mockup_subgroup_template_id", "mockup_subgroup", ["template_id"])
    op.create_index("ix_mockup_subgroup_tenant_id", "mockup_subgroup", ["tenant_id"])
    with op.batch_alter_table("mockup_photo") as table:
        table.add_column(sa.Column("subgroup_id", sa.Uuid(), nullable=True))
        table.create_foreign_key("fk_mockup_photo_subgroup_id", "mockup_subgroup", ["subgroup_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_mockup_photo_subgroup_id", "mockup_photo", ["subgroup_id"])
    op.add_column("mockup_design", sa.Column("subgroup", sa.Text(), nullable=True))
    op.add_column("mockup_design", sa.Column("pair_base", sa.Text(), nullable=True))
    op.add_column("mockup_design", sa.Column("ink", sa.Text(), nullable=True))
    op.create_table(
        "mockup_design_version",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("design_id", sa.Uuid(), sa.ForeignKey("mockup_design.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subgroup", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("design_key", sa.Text(), nullable=True),
        sa.Column("back_key", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("has_alpha", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ink", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mockup_design_version_design_id", "mockup_design_version", ["design_id"])
    # One default subgroup per existing template; its photos belong to it.
    bind = op.get_bind()
    template = sa.table("mockup_template", sa.column("id", sa.Uuid()), sa.column("tenant_id", sa.Uuid()), sa.column("name", sa.Text()))
    subgroup = sa.table("mockup_subgroup", sa.column("id", sa.Uuid()), sa.column("tenant_id", sa.Uuid()),
                        sa.column("template_id", sa.Uuid()), sa.column("name", sa.Text()), sa.column("use_on", sa.Text()),
                        sa.column("position", sa.Integer()))
    photo = sa.table("mockup_photo", sa.column("template_id", sa.Uuid()), sa.column("subgroup_id", sa.Uuid()))
    for tid, tenant_id, name in bind.execute(sa.select(template.c.id, template.c.tenant_id, template.c.name)).fetchall():
        sid = uuid.uuid4()
        bind.execute(subgroup.insert().values(id=sid, tenant_id=tenant_id, template_id=tid, name=name, use_on="any", position=0))
        bind.execute(photo.update().where(photo.c.template_id == tid).values(subgroup_id=sid))


def downgrade() -> None:
    op.drop_index("ix_mockup_design_version_design_id", table_name="mockup_design_version")
    op.drop_table("mockup_design_version")
    op.drop_column("mockup_design", "ink")
    op.drop_column("mockup_design", "pair_base")
    op.drop_column("mockup_design", "subgroup")
    op.drop_index("ix_mockup_photo_subgroup_id", table_name="mockup_photo")
    with op.batch_alter_table("mockup_photo") as table:
        table.drop_constraint("fk_mockup_photo_subgroup_id", type_="foreignkey")
        table.drop_column("subgroup_id")
    op.drop_index("ix_mockup_subgroup_tenant_id", table_name="mockup_subgroup")
    op.drop_index("ix_mockup_subgroup_template_id", table_name="mockup_subgroup")
    op.drop_table("mockup_subgroup")
