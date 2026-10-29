from __future__ import annotations

from pathlib import Path

import verify


def test_a_pinned_module_passes(tmp_path: Path):
    (tmp_path / "versions.tf").write_text(
        'terraform {\n  required_version = ">= 1.9"\n  required_providers {\n'
        '    azurerm = {\n      source  = "hashicorp/azurerm"\n      version = "~> 4.0"\n    }\n  }\n}\n',
        encoding="utf-8",
    )
    assert verify.pins(tmp_path) == []


def test_missing_terraform_version_and_provider_version_are_reported(tmp_path: Path):
    (tmp_path / "versions.tf").write_text(
        'terraform {\n  required_providers {\n    aws = {\n      source = "hashicorp/aws"\n    }\n  }\n}\n',
        encoding="utf-8",
    )
    found = verify.pins(tmp_path)
    assert any("required_version" in line for line in found)
    assert any("provider aws has no version constraint" in line for line in found)


def test_the_roots_of_this_repository_are_pinned():
    for root in verify.ROOTS:
        assert verify.pins(root) == [], root
