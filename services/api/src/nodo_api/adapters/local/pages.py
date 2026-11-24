"""SQLite persistence for cited Wiki Pages (ADR-031)."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from nodo_api.adapters.local.db import LocalDatabase, from_text, to_text
from nodo_api.adapters.memory.pages import _redact
from nodo_api.domain.pages import (
    Page,
    PageCitation,
    PageClaim,
    PageKind,
    PageProposal,
    PageProvenance,
    PageRevision,
    PageStatus,
)
from nodo_api.domain.sources import Locator
from nodo_api.errors import ConflictError, NotFoundError


def _page(row: sqlite3.Row) -> Page:
    return Page(
        row["id"], row["workspace_id"], row["owner_id"], PageKind(row["kind"]), row["subject_id"],
        PageStatus(row["status"]), from_text(row["created_at"]), row["current_revision"],
    )


class SqlitePageRepository:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db

    def _get(self, conn: sqlite3.Connection, workspace_id: str, owner_id: str, page_id: str) -> Page:
        row = conn.execute(
            "SELECT * FROM pages WHERE workspace_id = ? AND owner_id = ? AND id = ?",
            (workspace_id, owner_id, page_id),
        ).fetchone()
        if row is None:
            raise NotFoundError
        return _page(row)

    def put_proposal(self, page: Page, proposal: PageProposal) -> None:
        if proposal.page_id != page.id:
            raise ValueError("Page proposal belongs to another Page")
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM pages WHERE workspace_id = ? AND id = ?", (page.workspace_id, page.id)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (page.workspace_id, page.id, page.owner_id, page.kind.value, page.subject_id,
                     page.status.value, page.current_revision, to_text(page.created_at)),
                )
            elif _page(row).owner_id != page.owner_id:
                raise ConflictError
            conn.execute("DELETE FROM page_proposals WHERE workspace_id = ? AND page_id = ?", (page.workspace_id, page.id))
            conn.execute(
                "INSERT INTO page_proposals VALUES (?, ?, ?, ?, ?, ?)",
                (page.workspace_id, page.id, proposal.id, proposal.provenance.model_version,
                 proposal.provenance.prompt_version, to_text(proposal.created_at)),
            )
            self._write_claims(conn, page.workspace_id, page.id, proposal.claims, proposal_id=proposal.id)

    def get_owned(self, workspace_id: str, owner_id: str, page_id: str) -> Page:
        with self._db.transaction(write=False) as conn:
            return self._get(conn, workspace_id, owner_id, page_id)

    def proposal(self, workspace_id: str, owner_id: str, page_id: str) -> PageProposal | None:
        with self._db.transaction(write=False) as conn:
            self._get(conn, workspace_id, owner_id, page_id)
            row = conn.execute(
                "SELECT * FROM page_proposals WHERE workspace_id = ? AND page_id = ?", (workspace_id, page_id)
            ).fetchone()
            if row is None:
                return None
            return PageProposal(row["id"], page_id, self._claims(conn, workspace_id, page_id, proposal_id=row["id"]),
                PageProvenance(row["model_version"], row["prompt_version"]), from_text(row["created_at"]))

    def append_revision(self, workspace_id: str, owner_id: str, revision: PageRevision) -> Page:
        with self._db.transaction() as conn:
            page = self._get(conn, workspace_id, owner_id, revision.page_id)
            expected = (page.current_revision or 0) + 1
            if revision.number != expected:
                raise ConflictError
            conn.execute(
                "INSERT INTO page_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (workspace_id, revision.page_id, revision.number, revision.body_md, revision.author_id,
                 to_text(revision.created_at), revision.provenance.model_version, revision.provenance.prompt_version),
            )
            self._write_claims(conn, workspace_id, revision.page_id, revision.claims, rev=revision.number)
            conn.execute("DELETE FROM page_proposals WHERE workspace_id = ? AND page_id = ?", (workspace_id, revision.page_id))
            conn.execute("UPDATE pages SET status = 'approved', current_revision = ? WHERE workspace_id = ? AND id = ?", (revision.number, workspace_id, revision.page_id))
            return replace(page, status=PageStatus.APPROVED, current_revision=revision.number)

    def revisions(self, workspace_id: str, owner_id: str, page_id: str) -> tuple[PageRevision, ...]:
        with self._db.transaction(write=False) as conn:
            self._get(conn, workspace_id, owner_id, page_id)
            rows = conn.execute("SELECT * FROM page_revisions WHERE workspace_id = ? AND page_id = ? ORDER BY rev DESC", (workspace_id, page_id)).fetchall()
            return tuple(PageRevision(page_id, r["rev"], r["body_md"], r["author_id"], from_text(r["created_at"]), self._claims(conn, workspace_id, page_id, rev=r["rev"]), PageProvenance(r["model_version"], r["prompt_version"])) for r in rows)

    def mark_stale_for_source(self, workspace_id: str, source_id: str) -> tuple[str, ...]:
        with self._db.transaction() as conn:
            rows = conn.execute("SELECT DISTINCT p.id FROM pages p JOIN page_citations c ON c.workspace_id = p.workspace_id AND c.page_id = p.id WHERE p.workspace_id = ? AND p.status = 'approved' AND c.source_id = ?", (workspace_id, source_id)).fetchall()
            ids = tuple(row["id"] for row in rows)
            if ids:
                conn.executemany("UPDATE pages SET status = 'stale' WHERE workspace_id = ? AND id = ?", [(workspace_id, page_id) for page_id in ids])
            return ids

    def redact_source(self, workspace_id: str, source_id: str) -> tuple[str, ...]:
        with self._db.transaction() as conn:
            rows = conn.execute("SELECT DISTINCT page_id FROM page_citations WHERE workspace_id = ? AND source_id = ?", (workspace_id, source_id)).fetchall()
            ids = tuple(row["page_id"] for row in rows)
            for page_id in ids:
                page = self._get_any(conn, workspace_id, page_id)
                for revision in self._revisions_any(conn, workspace_id, page_id):
                    if any(c.source_id == source_id for claim in revision.claims for c in claim.citations):
                        redacted = _redact(revision, source_id)
                        self._replace_revision(conn, workspace_id, redacted)
                conn.execute("DELETE FROM page_proposals WHERE workspace_id = ? AND page_id = ?", (workspace_id, page_id))
                conn.execute("UPDATE pages SET status = 'stale' WHERE workspace_id = ? AND id = ?", (workspace_id, page_id))
                if page.kind is PageKind.SOURCE_SUMMARY and page.current_revision is None:
                    conn.execute("DELETE FROM pages WHERE workspace_id = ? AND id = ?", (workspace_id, page_id))
            return ids

    def _get_any(self, conn, workspace_id, page_id):
        row = conn.execute("SELECT * FROM pages WHERE workspace_id = ? AND id = ?", (workspace_id, page_id)).fetchone()
        if row is None:
            raise NotFoundError
        return _page(row)

    def _revisions_any(self, conn, workspace_id, page_id):
        rows = conn.execute("SELECT * FROM page_revisions WHERE workspace_id = ? AND page_id = ?", (workspace_id, page_id)).fetchall()
        return tuple(PageRevision(page_id, r["rev"], r["body_md"], r["author_id"], from_text(r["created_at"]), self._claims(conn, workspace_id, page_id, rev=r["rev"]), PageProvenance(r["model_version"], r["prompt_version"])) for r in rows)

    def _replace_revision(self, conn, workspace_id, revision):
        conn.execute("UPDATE page_revisions SET body_md = ? WHERE workspace_id = ? AND page_id = ? AND rev = ?", (revision.body_md, workspace_id, revision.page_id, revision.number))
        conn.execute("DELETE FROM page_claims WHERE workspace_id = ? AND page_id = ? AND rev = ?", (workspace_id, revision.page_id, revision.number))
        self._write_claims(conn, workspace_id, revision.page_id, revision.claims, rev=revision.number)

    def _write_claims(self, conn, workspace_id, page_id, claims, *, proposal_id=None, rev=None):
        for claim in claims:
            conn.execute("INSERT INTO page_claims VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (workspace_id, page_id, proposal_id, rev, claim.ordinal, claim.span[0], claim.span[1], claim.text, claim.text_hash, int(claim.machine_generated)))
            for ordinal, citation in enumerate(claim.citations):
                conn.execute("INSERT INTO page_citations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (workspace_id, page_id, proposal_id, rev, claim.ordinal, ordinal, citation.source_id, citation.fragment_id, citation.source_version, citation.locator.to_json(), citation.quote))

    def _claims(self, conn, workspace_id, page_id, *, proposal_id=None, rev=None):
        rows = conn.execute("SELECT * FROM page_claims WHERE workspace_id = ? AND page_id = ? AND proposal_id IS ? AND rev IS ? ORDER BY ordinal", (workspace_id, page_id, proposal_id, rev)).fetchall()
        result = []
        for row in rows:
            cites = conn.execute("SELECT * FROM page_citations WHERE workspace_id = ? AND page_id = ? AND proposal_id IS ? AND rev IS ? AND claim_ordinal = ? ORDER BY ordinal", (workspace_id, page_id, proposal_id, rev, row["ordinal"])).fetchall()
            result.append(PageClaim(row["ordinal"], (row["span_start"], row["span_end"]), row["text"], row["text_hash"], tuple(PageCitation(c["source_id"], c["fragment_id"], c["source_version"], Locator.from_json(c["locator"]), c["quote"]) for c in cites), bool(row["machine_generated"])))
        return tuple(result)
