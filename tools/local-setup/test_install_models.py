import hashlib

import install_models as im
import pytest


def test_the_checksum_of_a_file_matches_hashlib(tmp_path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"abc" * 1000)
    assert im.sha256_of(path) == hashlib.sha256(b"abc" * 1000).hexdigest()


@pytest.mark.parametrize("name", ["../evil", "/abs/path", "a/../../b"])
def test_archives_with_unsafe_paths_are_refused(name) -> None:
    with pytest.raises(SystemExit):
        im._check_members(["ok/file", name])


def test_every_github_download_is_pinned_and_over_https() -> None:
    for item in im.GITHUB:
        assert item.url.startswith("https://") and len(item.sha256) == 64


def test_a_present_file_with_the_wrong_checksum_counts_as_missing(tmp_path) -> None:
    item = im.Download("x", "x", "https://e/x", "0" * 64, 1, "x.bin")
    (tmp_path / "x.bin").write_bytes(b"data")
    assert not im.present(item, tmp_path)
