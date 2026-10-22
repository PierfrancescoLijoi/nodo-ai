from __future__ import annotations

import json
from pathlib import Path

from nodo_api.main import create_app


def main() -> None:
    target = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "openapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
