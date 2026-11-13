"""Original files for tests and the demo backend: the same store as the local one, on a private
temporary folder that is removed by close() or, at the latest, when the store is garbage collected
or the process ends.

The folder is real because the document parser reads a path, and a copy of the rules would be a
second place for the path and atomic-write checks to drift. The shared contract suite still runs
against both classes, but it is one implementation twice, not two independent ones."""

from __future__ import annotations

import shutil
import tempfile
import weakref
from typing import Any

from nodo_api.adapters.local.source_blobs import LocalSourceBlobStore


class MemorySourceBlobStore(LocalSourceBlobStore):
    def __init__(self, **options: Any) -> None:
        folder = tempfile.mkdtemp(prefix="nodo-source-blobs-")
        self._cleanup = weakref.finalize(self, shutil.rmtree, folder, True)
        super().__init__(folder, **options)

    def close(self) -> None:
        """Remove the temporary folder now; safe to call twice."""
        self._cleanup()
