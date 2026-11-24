"""Obsidian-compatible export of authoritative Wiki Pages (FR-18)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
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
        manifest = {
            "format": "nodo-vault-v1",
            "files": {
                str(path).replace("\\", "/"): hashlib.sha256(text.encode("utf-8")).hexdigest()
                for path, text in sorted(files.items(), key=lambda item: str(item[0]))
            },
        }
        files[Path("manifest.json")] = json.dumps(manifest, sort_keys=True, indent=2) + "\n"
        return files

    def write(self, workspace_id: str, owner_id: str, directory: Path) -> tuple[Path, ...]:
        """Write only this export's managed paths into an existing, explicit vault directory.

        The previous manifest is the authority for cleanup.  Arbitrary or malformed paths in it
        are ignored, so an export can never remove a user's unrelated vault note.
        """
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError("Vault export directory must be an existing absolute directory")
        if directory.is_symlink():
            raise ValueError("Vault export directory cannot be a symlink")
        root = directory.resolve(strict=True)
        files = self.render(workspace_id, owner_id)
        previous = self._previous_paths(root)
        current = set(files)
        for relative, text in files.items():
            target = self._target(root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent, delete=False
            ) as handle:
                handle.write(text)
                temporary = Path(handle.name)
            os.replace(temporary, target)
        for relative in previous - current:
            target = self._target(root, relative)
            if target.is_file():
                target.unlink()
        return tuple(sorted((self._target(root, relative) for relative in current), key=str))

    @staticmethod
    def _target(root: Path, relative: Path) -> Path:
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Vault export path is not relative")
        target = root.joinpath(relative)
        resolved = target.resolve(strict=False)
        if root not in resolved.parents and resolved != root:
            raise ValueError("Vault export path escapes directory")
        return target

    @staticmethod
    def _previous_paths(root: Path) -> set[Path]:
        manifest = root / "manifest.json"
        if not manifest.is_file():
            return set()
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            entries = data["files"] if data.get("format") == "nodo-vault-v1" else {}
        except (OSError, ValueError, AttributeError):
            return set()
        return {
            Path(name)
            for name in entries
            if isinstance(name, str)
            and not Path(name).is_absolute()
            and ".." not in Path(name).parts
        }
