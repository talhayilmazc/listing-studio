"""Where a batch's listings go: chosen once, as groups or shops (B4 "Where and when").

``upload_batch.send_to`` = ``{"group_ids": [...], "connection_ids": [...]}``: every approved
listing of the batch goes to every shop of those groups and to those shops, each shop once
(the owner, 2026-10-22: no splitting; 6 listings and 2 shops are 12 drafts). NULL: each
listing to its profile's main shop, as before. ``generated_content.send_to``, same shape, is
one listing's own exception ("→ GROUP 1 · change"); NULL follows its batch.

The shops ticked per listing on the old review matrix (``generated_content.target_shops``)
carry over exactly: a batch whose listings all had the same ticks gets them as its own
``send_to``; otherwise each listing with ticks keeps them as its exception. The ticks are
then cleared (they are read from ``send_to`` from now on). Nothing is sent; schedules,
plans and drafts are untouched.

Downgrade: every listing's shops (its exception, else its batch's; groups as their member
shops at that moment) are written back as ticks, and the two columns go.

Revision ID: 0084_send_to
Revises: 0083_listing_shop_from_profile
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0084_send_to"
down_revision: str | None = "0083_listing_shop_from_profile"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

content = sa.table("generated_content", sa.column("id"), sa.column("batch_id"),
                   sa.column("target_shops", sa.JSON()), sa.column("send_to", sa.JSON()))
batch = sa.table("upload_batch", sa.column("id"), sa.column("send_to", sa.JSON()))
member = sa.table("shop_group_member", sa.column("group_id"), sa.column("connection_id"))


def _ids(values) -> list[str]:  # noqa: ANN001
    out: list[str] = []
    for value in values or []:
        if str(value) not in out:
            out.append(str(value))
    return out


def upgrade() -> None:
    op.add_column("upload_batch", sa.Column("send_to", JSON, nullable=True))
    op.add_column("generated_content", sa.Column("send_to", JSON, nullable=True))
    bind = op.get_bind()
    by_batch: dict[str, list[tuple[object, list[str] | None]]] = {}
    raw: dict[str, object] = {}
    for cid, batch_id, ticks in bind.execute(sa.select(content.c.id, content.c.batch_id, content.c.target_shops)):
        by_batch.setdefault(str(batch_id), []).append((cid, _ids(ticks) if ticks is not None else None))
        raw[str(batch_id)] = batch_id
    for key, rows in by_batch.items():
        ticked = [t for _, t in rows if t is not None]
        if not ticked:
            continue
        if len(ticked) == len(rows) and all(sorted(t) == sorted(ticked[0]) for t in ticked):
            # Every listing had the same shops: that is the batch's choice.
            bind.execute(batch.update().where(batch.c.id == raw[key])
                         .values(send_to={"group_ids": [], "connection_ids": ticked[0]}))
        else:
            for cid, ticks in rows:
                if ticks is not None:
                    bind.execute(content.update().where(content.c.id == cid)
                                 .values(send_to={"group_ids": [], "connection_ids": ticks}))
        for cid, ticks in rows:
            if ticks is not None:
                bind.execute(content.update().where(content.c.id == cid).values(target_shops=None))


def downgrade() -> None:
    bind = op.get_bind()
    members: dict[str, list[str]] = {}
    for gid, cid in bind.execute(sa.select(member.c.group_id, member.c.connection_id)):
        members.setdefault(str(gid), []).append(str(cid))
    batches = {str(b): s for b, s in bind.execute(sa.select(batch.c.id, batch.c.send_to))}

    def shops(send_to) -> list[str]:  # noqa: ANN001
        out: list[str] = []
        for gid in (send_to or {}).get("group_ids") or []:
            out += members.get(str(gid), [])
        out += [str(c) for c in (send_to or {}).get("connection_ids") or []]
        return _ids(out)

    for cid, batch_id, own in list(bind.execute(sa.select(content.c.id, content.c.batch_id, content.c.send_to))):
        chosen = own if own is not None else batches.get(str(batch_id))
        if chosen is not None:
            bind.execute(content.update().where(content.c.id == cid).values(target_shops=shops(chosen)))
    op.drop_column("generated_content", "send_to")
    op.drop_column("upload_batch", "send_to")
