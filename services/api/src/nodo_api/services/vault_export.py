"""Obsidian-compatible export of authoritative Wiki Pages (FR-18)."""

from __future__ import annotations

import re
from pathlib import Path

from nodo_api.ports import PageRepository

_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def page_filename(page_id: str) -> str:
    return _SAFE.sub("-", page_id).strip(".-") or "page"


class VaultExportService:
    def __init__(self, pages: PageRepository) -> None:
        self._pages = pages

    def render(self, workspace_id: str, owner_id: str) -> dict[Path, str]:
        files: dict[Path, str] = {}
        for page in self._pages.list_owned(workspace_id, owner_id):
            if page.current_revision is None:
                continue
            revision = self._pages.revisions(workspace_id, owner_id, page.id)[0]
            files[Path("pages") / f"{page_filename(page.id)}.md"] = (
                "---\n"
                f"id: {page.id}\nkind: {page.kind.value}\nstatus: {page.status.value}\n"
                f"subject: {page.subject_id}\nrevision: {revision.number}\n---\n\n"
                f"{revision.body_md}\n"
            )
        return files
