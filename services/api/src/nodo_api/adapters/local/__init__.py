"""Local backend: the same ports as Azure, implemented on this computer (ADR-014).

SQLite for data and graph, the file system for audio, an in-process queue, FTS5 plus vectors for
search, and local models for speech, embeddings and language. Nothing here talks to a cloud."""
