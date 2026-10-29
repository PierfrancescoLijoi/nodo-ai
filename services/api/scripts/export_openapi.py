from __future__ import annotations

import json
from pathlib import Path

from nodo_api.config import Settings
from nodo_api.main import create_app

# Only the schema is generated here, so a throwaway development configuration is fine.
SCHEMA_SETTINGS = Settings(
    environment="test", auth_mode="dev", attestation_hmac_key="schema-export-only-key"
)


def main() -> None:
    target = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "openapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(create_app(SCHEMA_SETTINGS).openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
