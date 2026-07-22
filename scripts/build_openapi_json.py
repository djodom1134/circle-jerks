#!/usr/bin/env python
"""Generate the JSON form of the hand-written partner OpenAPI document.

docs/api/openapi.yaml is the single source of truth. The frontend cannot parse
YAML without a new dependency, and the backend should not re-read the file on
every request, so the JSON is generated here and committed.

    backend/.venv/bin/python scripts/build_openapi_json.py
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "api" / "openapi.yaml"
TARGETS = (
    ROOT / "frontend" / "src" / "generated" / "openapi.json",
    ROOT / "backend" / "app" / "generated" / "openapi.json",
)


def build() -> str:
    document = yaml.safe_load(SOURCE.read_text())
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main() -> int:
    payload = build()
    for target in TARGETS:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload)
        print(f"wrote {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
