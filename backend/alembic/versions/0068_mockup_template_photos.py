"""Templates hold many photos; every photo's print areas are confirmed once.

* ``mockup_template`` (one photo) becomes ``mockup_photo``, and ``mockup_template_set``
  (a colour set) becomes ``mockup_template``: a named group of photos. Every set
  becomes a template with its photos, in their order; every photo that was in no set
  becomes a template of its own, named after it. Nothing the seller made is lost.
* ``mockup_photo.template_id`` (was ``set_id``) is required; deleting a template
  deletes its photos.
* ``mockup_photo.confirmed_at``: when the seller pressed "Looks right". A photo whose
  print area the seller had already set by hand (or given to its set) counts as
  confirmed; one still as found is not, and is shown as "Needs your check" until the
  seller looks at it: it is not used for mockups until then.
* ``mockup_photo.foreground_key``: where garments are, for "Add area" (photos prepared
  from now on).
* ``mockup_photo.garments``: every photo keeps its print areas here (one or more);
  ``print_area`` stays the first one's.
* ``mockup_run.template_ids`` keeps the chosen templates, ``photo_ids`` the photos it
  printed on (what ``template_ids`` held), ``skipped`` the unconfirmed photos it left out.
* ``mockup_output.photo_id`` (was ``template_id``) and ``photo_name``;
  ``asset.mockup_photo_id`` (was ``mockup_template_id``).

Downgrade: templates become sets again; a template of one photo named like its photo
(as this migration made them) becomes a photo in no set.

Revision ID: 0068_mockup_template_photos
Revises: 0067_mockup_garments
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0068_mockup_template_photos"
down_revision: str | None = "0067_mockup_garments"
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    # Names: the photo table first, so the old set table can take its name.
    op.rename_table("mockup_template", "mockup_photo")
    op.execute("ALTER INDEX ix_mockup_template_tenant_id RENAME TO ix_mockup_photo_tenant_id")
    op.execute("ALTER INDEX ix_mockup_template_set_id RENAME TO ix_mockup_photo_template_id")
    op.rename_table("mockup_template_set", "mockup_template")
    op.execute("ALTER INDEX ix_mockup_template_set_tenant_id RENAME TO ix_mockup_template_tenant_id")
    op.alter_column("mockup_photo", "set_id", new_column_name="template_id")
    op.add_column("mockup_photo", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("mockup_photo", sa.Column("foreground_key", sa.Text(), nullable=True))

    # A template of its own for every photo in no set, in the photo's place.
    loose = bind.execute(sa.text(
        "SELECT id, tenant_id, name, position, created_at FROM mockup_photo WHERE template_id IS NULL"
    )).fetchall()
    for photo_id, tenant_id, name, position, created_at in loose:
        template_id = uuid.uuid4()
        bind.execute(sa.text(
            "INSERT INTO mockup_template (id, tenant_id, name, position, created_at) "
            "VALUES (:id, :tenant, :name, :position, :created)"
        ), {"id": template_id, "tenant": tenant_id, "name": name, "position": position, "created": created_at})
        bind.execute(sa.text("UPDATE mockup_photo SET template_id = :t WHERE id = :p"), {"t": template_id, "p": photo_id})
    # Templates in the seller's order: by their first photo's place, then when made.
    order = bind.execute(sa.text(
        "SELECT t.id, t.tenant_id FROM mockup_template t "
        "LEFT JOIN mockup_photo p ON p.template_id = t.id "
        "GROUP BY t.id, t.tenant_id, t.position, t.created_at "
        "ORDER BY t.tenant_id, MIN(COALESCE(p.position, t.position)), t.created_at"
    )).fetchall()
    places: dict[object, int] = {}
    for template_id, tenant_id in order:
        places[tenant_id] = places.get(tenant_id, 0) + 1
        bind.execute(sa.text("UPDATE mockup_template SET position = :n WHERE id = :t"),
                     {"n": places[tenant_id], "t": template_id})

    with op.batch_alter_table("mockup_photo") as table:
        table.drop_constraint("fk_mockup_template_set_id", type_="foreignkey")
        table.alter_column("template_id", nullable=False)
        table.create_foreign_key("fk_mockup_photo_template_id", "mockup_template", ["template_id"], ["id"],
                                 ondelete="CASCADE")

    # Every photo keeps its areas in ``garments``; one set by hand counts as confirmed.
    rows = bind.execute(sa.text(
        "SELECT id, print_area, print_area_source, print_area_confidence, garments, analysis, status "
        "FROM mockup_photo"
    )).fetchall()
    for photo_id, area, source, confidence, garments, analysis, status in rows:
        area = _json(area)
        garments = _json(garments)
        analysis = _json(analysis) or {}
        if not garments and area:
            garments = [{"print_area": area, "detected_area": analysis.get("detected_area") or area,
                         "source": source or "default", "confidence": confidence}]
        by_hand = bool(garments) and any((g.get("source") or "default") == "seller" for g in garments)
        bind.execute(sa.text(
            "UPDATE mockup_photo SET garments = CAST(:g AS JSONB), "
            "confirmed_at = CASE WHEN :c THEN now() ELSE NULL END WHERE id = :p"
        ), {"g": json.dumps(garments) if garments else None, "c": by_hand and status == "ready", "p": photo_id})

    op.alter_column("mockup_run", "template_ids", new_column_name="photo_ids")
    op.add_column("mockup_run", sa.Column("template_ids", JSONB, nullable=True))
    op.add_column("mockup_run", sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"))
    for run_id, photo_ids in bind.execute(sa.text("SELECT id, photo_ids FROM mockup_run")).fetchall():
        ids = [str(i) for i in (_json(photo_ids) or [])]
        templates: list[str] = []
        for pid in ids:
            found = bind.execute(sa.text("SELECT template_id FROM mockup_photo WHERE id = CAST(:p AS UUID)"),
                                 {"p": pid}).scalar()
            if found is not None and str(found) not in templates:
                templates.append(str(found))
        bind.execute(sa.text("UPDATE mockup_run SET template_ids = CAST(:t AS JSONB) WHERE id = :r"),
                     {"t": json.dumps(templates), "r": run_id})
    op.alter_column("mockup_run", "template_ids", nullable=False)

    op.alter_column("mockup_output", "template_id", new_column_name="photo_id")
    op.add_column("mockup_output", sa.Column("photo_name", sa.Text(), nullable=True))
    op.alter_column("asset", "mockup_template_id", new_column_name="mockup_photo_id")


def downgrade() -> None:
    bind = op.get_bind()
    op.alter_column("asset", "mockup_photo_id", new_column_name="mockup_template_id")
    op.drop_column("mockup_output", "photo_name")
    op.alter_column("mockup_output", "photo_id", new_column_name="template_id")
    op.drop_column("mockup_run", "skipped")
    op.drop_column("mockup_run", "template_ids")
    op.alter_column("mockup_run", "photo_ids", new_column_name="template_ids")

    with op.batch_alter_table("mockup_photo") as table:
        table.drop_constraint("fk_mockup_photo_template_id", type_="foreignkey")
        table.alter_column("template_id", nullable=True)
        table.create_foreign_key("fk_mockup_template_set_id", "mockup_template", ["template_id"], ["id"],
                                 ondelete="SET NULL")
    # A template of one photo with the photo's name was a photo in no set.
    singles = bind.execute(sa.text(
        "SELECT t.id FROM mockup_template t JOIN mockup_photo p ON p.template_id = t.id "
        "GROUP BY t.id, t.name HAVING COUNT(*) = 1 AND MIN(p.name) = t.name"
    )).fetchall()
    for (template_id,) in singles:
        bind.execute(sa.text("UPDATE mockup_photo SET template_id = NULL WHERE template_id = :t"), {"t": template_id})
        bind.execute(sa.text("DELETE FROM mockup_template WHERE id = :t"), {"t": template_id})
    # One garment: back to the earlier form (no list).
    bind.execute(sa.text(
        "UPDATE mockup_photo SET garments = NULL WHERE garments IS NOT NULL AND jsonb_array_length(garments) <= 1"
    ))
    op.drop_column("mockup_photo", "foreground_key")
    op.drop_column("mockup_photo", "confirmed_at")
    op.alter_column("mockup_photo", "template_id", new_column_name="set_id")
    op.execute("ALTER INDEX ix_mockup_template_tenant_id RENAME TO ix_mockup_template_set_tenant_id")
    op.rename_table("mockup_template", "mockup_template_set")
    op.execute("ALTER INDEX ix_mockup_photo_template_id RENAME TO ix_mockup_template_set_id")
    op.execute("ALTER INDEX ix_mockup_photo_tenant_id RENAME TO ix_mockup_template_tenant_id")
    op.rename_table("mockup_photo", "mockup_template")


def _json(value: object) -> object:
    """A JSON column as Python (psycopg returns JSONB parsed; a string is parsed here)."""
    if isinstance(value, str):
        return json.loads(value)
    return value
