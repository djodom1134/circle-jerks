"""The continuous worker path must detect landings too.

`run_detectors_for_monitor` runs the live `detect_events` path when called
without a window (the worker at worker.py:77/131). `detect_events` cannot emit
landings — they require a 5-min settle window — so without an explicit windowed
landing pass, the worker would persist touch-and-gos continuously but landings
only on the /scan path, biasing "% did not stop" high for airports that aren't
actively viewed. This pins the wiring; the landing detection logic itself is
covered by tests/test_operations.py.
"""
from __future__ import annotations

import inspect

from app import services


def test_worker_live_path_runs_landing_detection():
    src = inspect.getsource(services.run_detectors_for_monitor)
    # The windowed landing pass must be invoked...
    assert "detect_landings_over_period" in src
    # ...and gated to the live branch (no scan window), where detect_events runs.
    assert "if start_ts is None or end_ts is None:" in src


def test_services_imports_detect_landings_over_period():
    assert hasattr(services, "detect_landings_over_period")
