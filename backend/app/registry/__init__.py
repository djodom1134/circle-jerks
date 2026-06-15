"""FAA aircraft registry enrichment.

Owns the FAA-registry-derived view of an aircraft:
- normalization of N-numbers, ICAO Mode-S hex, and callsigns
- decoding of FAA single-letter type/engine/status codes into human labels
- heuristic owner-type inference from registrant names
- the importer that ingests FAA's ReleasableAircraft.zip into SQLite
- the profile resolver that merges ADS-B observations with registry rows

Public surface:
- normalize, codes, owner_type, importer, profile
"""

from . import codes, normalize, owner_type, profile  # noqa: F401
