from nodo_api.adapters.local.model_catalog import (
    CATALOG,
    HardwareTier,
    recommend,
    recommended_entries,
    remove,
)


def test_catalog_ids_are_unique_and_direct_downloads_are_pinned() -> None:
    assert len({entry.id for entry in CATALOG}) == len(CATALOG)
    for entry in CATALOG:
        if entry.installable:
            assert entry.url and entry.url.startswith("https://")
            assert entry.sha256 and len(entry.sha256) == 64


def test_hardware_recommendations_are_conservative() -> None:
    assert recommend(None) is HardwareTier.CPU
    assert recommend(6 * 1024**3) is HardwareTier.GPU_6GB
    assert recommend(12 * 1024**3) is HardwareTier.GPU_LARGE
    assert all(HardwareTier.CPU in entry.hardware for entry in recommended_entries(None))


def test_only_owned_paths_can_count_as_installed(tmp_path) -> None:
    entry = CATALOG[0]
    assert not entry.owned_paths(tmp_path)[0].exists()


def test_removal_can_only_remove_a_catalog_owned_path(tmp_path) -> None:
    entry = CATALOG[0]
    owned = entry.owned_paths(tmp_path)[0]
    owned.mkdir(parents=True)
    (owned / "model.bin").write_bytes(b"model")
    outside = tmp_path / "do-not-delete"
    outside.write_text("keep")
    remove(entry, tmp_path)
    assert not owned.exists() and outside.read_text() == "keep"
