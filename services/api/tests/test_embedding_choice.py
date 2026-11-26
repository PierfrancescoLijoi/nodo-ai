"""Which E5 model a data folder uses (vectors of different sizes must never be mixed)."""

from __future__ import annotations

import sqlite3

from nodo_api.adapters.local.models import EMBEDDING_PIN, choose_embedding


def _install(models, name):
    folder = models / "embeddings" / name
    folder.mkdir(parents=True)
    (folder / "model.int8.onnx").write_bytes(b"x")


def test_fresh_folder_gets_large_when_installed_and_keeps_it(tmp_path):
    models, data = tmp_path / "m", tmp_path / "d"
    _install(models, "multilingual-e5-base")
    assert choose_embedding(models, data) == "multilingual-e5-base"
    assert not (data / EMBEDDING_PIN).exists()  # nothing pinned while the folder is still empty
    _install(models, "multilingual-e5-large")
    assert choose_embedding(models, data) == "multilingual-e5-large"
    assert (data / EMBEDDING_PIN).read_text() == "multilingual-e5-large"
    assert choose_embedding(models, data) == "multilingual-e5-large"


def test_existing_index_stays_on_base_even_after_large_is_installed(tmp_path):
    models, data = tmp_path / "m", tmp_path / "d"
    data.mkdir()
    _install(models, "multilingual-e5-base")
    with sqlite3.connect(data / "nodo.db") as db:
        db.execute("CREATE TABLE search_segments (id INTEGER)")
        db.execute("INSERT INTO search_segments VALUES (1)")
    _install(models, "multilingual-e5-large")
    assert choose_embedding(models, data) == "multilingual-e5-base"
    assert (data / EMBEDDING_PIN).read_text() == "multilingual-e5-base"


def test_pinned_model_that_was_removed_is_not_swapped_for_another_size(tmp_path):
    models, data = tmp_path / "m", tmp_path / "d"
    data.mkdir()
    _install(models, "multilingual-e5-base")
    (data / EMBEDDING_PIN).write_text("multilingual-e5-large")
    assert choose_embedding(models, data) == "multilingual-e5-large"
