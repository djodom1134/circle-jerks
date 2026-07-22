"""Enforce scripts/verify_openapi_doc.py's drift check inside pytest.

docs/api/openapi.yaml is the single source of truth; scripts/build_openapi_json.py
generates it to the committed JSON copies read by the frontend docs panel and by
/v1/openapi.json (app/generated/openapi.json), and scripts/verify_openapi_doc.py
fails loudly when a committed copy drifts from the YAML. But there is no CI in
this repo, so that check only runs when a human remembers to run the script by
hand. This puts the same comparison inside pytest, so `pytest` alone catches a
stale generated file.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"

TARGETS = (
    "frontend/src/generated/openapi.json",
    "backend/app/generated/openapi.json",
)


def _load_build_openapi_json():
    """Import scripts/build_openapi_json.py by path.

    scripts/ is not on the backend's pythonpath (backend/pyproject.toml sets
    `pythonpath = ["."]`, i.e. just `backend/`), so this loads the module
    directly from its file rather than adding another directory to sys.path
    for the whole test session.
    """
    spec = importlib.util.spec_from_file_location(
        "build_openapi_json", SCRIPTS_DIR / "build_openapi_json.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("target_name", TARGETS)
def test_generated_openapi_json_matches_the_yaml_source(target_name):
    """The committed JSON must equal what the generator produces from
    docs/api/openapi.yaml right now. This is the drift gate
    scripts/verify_openapi_doc.py enforces; a stale copy here means partners
    (or /v1/openapi.json) are served a contract the YAML no longer describes.
    """
    module = _load_build_openapi_json()
    expected = module.build()
    target = ROOT / target_name
    assert target.exists(), f"{target_name} is missing; run scripts/build_openapi_json.py"
    assert target.read_text() == expected, (
        f"{target_name} is stale; run scripts/build_openapi_json.py"
    )
