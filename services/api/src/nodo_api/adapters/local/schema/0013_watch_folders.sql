CREATE TABLE watch_folders (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    path TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, owner_id, path)
);
CREATE TABLE watch_files (
    folder_id TEXT NOT NULL REFERENCES watch_folders(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT,
    source_id TEXT,
    seen_stable_count INTEGER NOT NULL DEFAULT 1,
    linked_only INTEGER NOT NULL DEFAULT 0 CHECK (linked_only IN (0, 1)),
    PRIMARY KEY (folder_id, rel_path)
);
