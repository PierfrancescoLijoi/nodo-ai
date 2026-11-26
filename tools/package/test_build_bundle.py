"""Run: services/api/.venv/Scripts/python -m pytest tools/package/test_build_bundle.py"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_bundle import closure, norm  # noqa: E402


def test_closure_follows_requirements_and_skips_other_platforms():
    names = closure("pytest", ())
    assert "pluggy" in names and "iniconfig" in names
    assert "pytest" in names and norm("Typing_Extensions") == "typing-extensions"


def test_closure_of_the_engine_excludes_pytorch():
    names = closure("nodo-api", ("local-models", "document"))
    assert "fastapi" in names and "onnxruntime" in names
    assert "torch" not in names and "transformers" not in names
