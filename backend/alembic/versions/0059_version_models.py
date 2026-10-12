"""The models that wrote each listing: on the content, and on each content version.

``generated_content.vision_model`` (beside ``model_used``, the content model) and
``content_version.vision_model`` / ``content_model``: which model read the design
and which wrote the text, so a change of model can be measured by version.
Internal: no seller endpoint returns them.

Existing versions get the content model of the listing they were made from,
where that listing is still there; the vision model was not kept before now and
stays NULL. The count is printed.

Revision ID: 0059_version_models
Revises: 0058_version_sku
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0059_version_models"
down_revision: str | None = "0058_version_sku"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generated_content", sa.Column("vision_model", sa.Text()))
    op.add_column("content_version", sa.Column("vision_model", sa.Text()))
    op.add_column("content_version", sa.Column("content_model", sa.Text()))
    filled = op.get_bind().execute(
        sa.text(
            """
            UPDATE content_version AS v
               SET content_model = g.model_used
              FROM listing_publication AS p
              JOIN generated_content AS g ON g.id = p.content_id
             WHERE p.id = v.publication_id
               AND v.reason IN ('generated', 'edited', 'sku_edited')
               AND g.model_used IS NOT NULL
            """
        )
    )
    print(f"0059: content model filled in on {filled.rowcount} existing version(s); their vision model is not known")


def downgrade() -> None:
    op.drop_column("content_version", "content_model")
    op.drop_column("content_version", "vision_model")
    op.drop_column("generated_content", "vision_model")
