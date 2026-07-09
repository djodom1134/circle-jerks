import inspect

from app import services
from app.services import resolve_offender_owner


def test_resolve_offender_owner_override_wins():
    r = resolve_offender_owner("aa11", {"aa11": "individual"}, {"aa11": "flight_school"})
    assert r == {"owner_class": "flight_school", "owner_source": "community", "is_flight_school": True}


def test_resolve_offender_owner_inferred():
    r = resolve_offender_owner("bb22", {"bb22": "llc"}, {})
    assert r == {"owner_class": "llc", "owner_source": "inferred", "is_flight_school": False}


def test_resolve_offender_owner_unknown():
    r = resolve_offender_owner("cc33", {}, {})
    assert r == {"owner_class": "unknown", "owner_source": "inferred", "is_flight_school": False}


def test_enrich_offenders_wires_vnap_score():
    src = inspect.getsource(services.enrich_offenders)
    assert "compute_aircraft_compliance" in src
    assert "vnap_score" in src


def test_services_imports_vnap():
    assert hasattr(services, "vnap")
