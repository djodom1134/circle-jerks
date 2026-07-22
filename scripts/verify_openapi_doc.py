#!/usr/bin/env python
"""Check docs/api/openapi.yaml against the routes the app actually serves.

The hand-written partner document is richer than FastAPI's generated schema —
it carries descriptions, real examples, the error envelope, and the auth and
pagination rules. That richness is the reason it exists, and also the reason it
can silently drift once someone adds or renames a route.

This fails loudly on drift. Run it from the repo root:

    backend/.venv/bin/python scripts/verify_openapi_doc.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "api" / "openapi.yaml"

# So the sibling `build_openapi_json` import below resolves regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Import the app against a throwaway database so this never touches real data.
sys.path.insert(0, str(ROOT / "backend"))
os.environ.update(
    CIRCLEJERK_DATABASE_PATH=str(Path(tempfile.mkdtemp()) / "verify.sqlite3"),
    CIRCLEJERK_REDIS_URL="memory://",
    CIRCLEJERK_ENVIRONMENT="test",
)

import yaml  # noqa: E402
from fastapi.openapi.utils import get_openapi  # noqa: E402

from app.public_api import API_VERSION, router  # noqa: E402

# Declared on the sidecar, forwarded by our proxy, so it is documented for
# partners but never appears on our own route signature.
FORWARDED_ONLY_PARAMS = {"days"}
# Machinery, not part of the partner-facing surface.
SELF_DESCRIBING_PATHS = ("openapi.json", "docs")


def main() -> int:
    live = get_openapi(title="verify", version=API_VERSION, routes=router.routes)
    doc = yaml.safe_load(DOC.read_text())
    raw = DOC.read_text()

    problems: list[str] = []

    live_paths = {p for p in live["paths"] if not p.endswith(SELF_DESCRIBING_PATHS)}
    doc_paths = set(doc["paths"])

    for path in sorted(live_paths - doc_paths):
        problems.append(f"route {path} is served but undocumented")
    for path in sorted(doc_paths - live_paths):
        problems.append(f"{path} is documented but no such route exists")

    for path in sorted(live_paths & doc_paths):
        served = {q["name"] for q in live["paths"][path]["get"].get("parameters", [])}
        documented = set()
        for param in doc["paths"][path]["get"].get("parameters", []):
            name = param.get("name")
            if name is None:  # a $ref into components/parameters
                ref = param["$ref"].rsplit("/", 1)[-1]
                name = doc["components"]["parameters"][ref]["name"]
            documented.add(name)
        documented -= FORWARDED_ONLY_PARAMS
        if served != documented:
            problems.append(
                f"{path}: app takes {sorted(served)}, doc describes {sorted(documented)}"
            )

    for section, name in sorted(set(re.findall(r"#/components/(\w+)/(\w+)", raw))):
        if name not in doc.get("components", {}).get(section, {}):
            problems.append(f"dangling $ref: #/components/{section}/{name}")

    # The committed JSON is what the admin docs panel and /v1/openapi.json
    # serve. If it drifts from the YAML, partners read one contract while the
    # API advertises another.
    from build_openapi_json import TARGETS, build  # noqa: E402

    expected = build()
    for target in TARGETS:
        if not target.exists():
            problems.append(f"{target} is missing; run scripts/build_openapi_json.py")
        elif target.read_text() != expected:
            problems.append(
                f"{target} is stale; run scripts/build_openapi_json.py"
            )

    if problems:
        print(f"{DOC.relative_to(ROOT)} is out of sync with the app:\n")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(
        f"{DOC.relative_to(ROOT)}: {len(doc_paths)} paths, parameters, and $refs "
        f"all match the served app."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
