"""The compliance scanner: checks a listing's text and records what it finds.

Findings are stored as ``compliance_finding`` rows, one per problem. A
``blocking`` finding stops the listing from becoming a draft or going live (see
``etsy/publisher.py``), so the scanner is the backstop behind validation: even a
listing approved before a term was added to the list is caught at publish time.

Rules so far:

* ``trademark`` (blocking): a term from the trademark blocklist in the title, a
  tag or the description (v6 §B). Nothing while ``TRADEMARK_FILTER`` is off.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.trademarks import Blocklist, configured_blocklist, tenant_blocklist
from app.db.models import ComplianceFinding, ComplianceSeverity, GeneratedContent

TRADEMARK = "trademark"


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: ComplianceSeverity
    detail: str


def scan(
    title: str, tags: list[str], description: str, blocklist: Blocklist | None = None
) -> list[Finding]:
    """Every problem in this text. Pure: nothing is read from or written to the DB."""
    blocklist = configured_blocklist() if blocklist is None else blocklist
    findings: list[Finding] = []
    places = [("the title", title), *((f"the tag '{t}'", t) for t in tags), ("the description", description)]
    for where, text in places:
        for term in blocklist.find(text):
            findings.append(
                Finding(TRADEMARK, ComplianceSeverity.blocking, f"trademark '{term}' in {where}")
            )
    return findings


async def rescan(session: AsyncSession, content: GeneratedContent) -> list[Finding]:
    """Replace this listing's findings with a fresh scan of its current text.

    The caller commits. Run whenever the text changes or is about to be used.
    """
    findings = scan(
        content.title or "",
        list(content.tags or []),
        content.description or "",
        await tenant_blocklist(session, content.tenant_id),
    )
    await session.execute(
        delete(ComplianceFinding).where(ComplianceFinding.generated_content_id == content.id)
    )
    for f in findings:
        session.add(
            ComplianceFinding(
                tenant_id=content.tenant_id,
                generated_content_id=content.id,
                severity=f.severity,
                rule=f.rule,
                detail=f.detail,
            )
        )
    await session.flush()
    return findings
