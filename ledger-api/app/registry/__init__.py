"""Copied (not shared) from backend/app/registry — resolve_display_tail and
infer_owner_type are pure, self-contained helpers (stdlib re/dataclasses only)
needed by this service's operator_ledger. The main backend's registry
package stays in backend/app/registry; it does far more (importer, profile,
codes) that this service has no need of and must not depend on."""
from . import normalize, owner_type  # noqa: F401

