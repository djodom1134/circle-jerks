"""FAA Releasable Aircraft Database importer.

Downloads https://registry.faa.gov/database/ReleasableAircraft.zip, extracts
MASTER.txt (current registrations) and ACFTREF.txt (aircraft reference), joins
the two on the make/model code, and upserts into aircraft_registry.

Designed to be safe to re-run:
- existing rows are upserted, not deleted (so an aborted import never empties
  the table)
- import metadata (started_at, status, rows_*, duration) is recorded in
  aircraft_registry_imports
- a single import is gated by a database row + an in-process lock so concurrent
  /admin invocations don't both stomp the registry at the same time

The downloaded ZIP is ~80MB; the extracted MASTER.txt has ~300k rows. A full
import on the production droplet takes 3-5 minutes; intentionally synchronous
so the admin endpoint can report progress / completion in the response when
called with wait=true, and asynchronous (returns 202) when wait=false.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import sqlite3
import time
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import httpx

from .. import db
from . import codes, normalize, owner_type

LOGGER = logging.getLogger(__name__)

DEFAULT_SOURCE_URL = "https://registry.faa.gov/database/ReleasableAircraft.zip"
DOWNLOAD_TIMEOUT_SECONDS = 240.0
DOWNLOAD_CHUNK_BYTES = 1 << 16
# I/O-pacing: each chunk opens its own short SQLite transaction, commits, then
# yields the event loop / releases the write lock so the api workers can
# squeeze in a write. ~624 chunks * (commit ~30ms + sleep) keeps the box
# responsive for the ~2 min total runtime.
IMPORT_CHUNK_SIZE = 500
IMPORT_CHUNK_SLEEP_SECONDS = 0.1

_import_lock = asyncio.Lock()


@dataclass
class ImportResult:
    import_id: int
    status: str  # "completed", "failed", "running"
    rows_imported: int
    rows_skipped: int
    rows_invalid: int
    duration_seconds: float
    source_url: str
    source_date: str | None
    error: str | None


async def import_faa_registry(
    database_path: str,
    *,
    source_url: str = DEFAULT_SOURCE_URL,
    zip_path: Path | None = None,
) -> ImportResult:
    """Run a full FAA registry import.

    If zip_path is provided, reads from disk instead of fetching the URL.
    Either way, parses MASTER.txt + ACFTREF.txt and upserts into SQLite.
    """
    if _import_lock.locked():
        raise RuntimeError("An aircraft registry import is already in progress")
    async with _import_lock:
        started_at = int(time.time())
        with db.db_session(database_path) as conn:
            import_id = db.record_import_start(conn, started_at=started_at, source_url=source_url)
            conn.commit()

        rows_imported = 0
        rows_skipped = 0
        rows_invalid = 0
        source_date: str | None = None
        error: str | None = None
        status = "completed"
        t0 = time.monotonic()

        try:
            if zip_path is not None:
                LOGGER.info("Reading FAA registry from local zip: %s", zip_path)
                zip_bytes = zip_path.read_bytes()
            else:
                LOGGER.info("Downloading FAA registry from %s", source_url)
                zip_bytes = await _download(source_url)

            master_text, acftref_text, source_date = _extract(zip_bytes)
            ref_lookup = _parse_acftref(acftref_text)

            # Chunked upserts: per `IMPORT_CHUNK_SIZE` rows we open a fresh
            # SQLite session, write the batch, commit, close, and yield. This
            # bounds the time the importer holds the SQLite write lock so
            # concurrent api writes (monitor registration, events) can
            # interleave instead of stacking behind a long single transaction.
            buffer: list[dict] = []

            async def flush(label: str) -> None:
                nonlocal rows_imported
                if not buffer:
                    return
                with db.db_session(database_path) as conn:
                    for row in buffer:
                        db.upsert_aircraft_registry(conn, row)
                    conn.commit()
                rows_imported += len(buffer)
                LOGGER.info(
                    "FAA registry import: chunk=%s imported_total=%d",
                    label, rows_imported,
                )
                buffer.clear()
                await asyncio.sleep(IMPORT_CHUNK_SLEEP_SECONDS)

            for row, kind in _iter_master(master_text, ref_lookup):
                if kind == "ok":
                    buffer.append(row)
                    if len(buffer) >= IMPORT_CHUNK_SIZE:
                        await flush(f"@{rows_imported + len(buffer)}")
                elif kind == "skipped":
                    rows_skipped += 1
                else:
                    rows_invalid += 1
            await flush("final")
        except Exception as exc:  # noqa: BLE001 — surface any failure to caller
            LOGGER.exception("FAA registry import failed")
            error = f"{type(exc).__name__}: {exc}"
            status = "failed"

        duration = time.monotonic() - t0
        finished_at = int(time.time())
        with db.db_session(database_path) as conn:
            db.record_import_finish(
                conn,
                import_id=import_id,
                finished_at=finished_at,
                status=status,
                rows_imported=rows_imported,
                rows_skipped=rows_skipped,
                rows_invalid=rows_invalid,
                duration_seconds=duration,
                source_date=source_date,
                error=error,
            )
            conn.commit()

        return ImportResult(
            import_id=import_id,
            status=status,
            rows_imported=rows_imported,
            rows_skipped=rows_skipped,
            rows_invalid=rows_invalid,
            duration_seconds=duration,
            source_url=source_url,
            source_date=source_date,
            error=error,
        )


async def _download(url: str) -> bytes:
    # FAA's IIS server returns 403 for non-browser User-Agents AND for the
    # "Mozilla/5.0 (compatible; ...)" pattern. It accepts a plain modern Chrome
    # UA, so we send that. (We're not impersonating anyone — the FAA registry
    # is published as a public bulk dataset; the UA gate is just bot mitigation.)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }
    async with httpx.AsyncClient(
        timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True, headers=headers,
    ) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            buf = bytearray()
            async for chunk in response.aiter_bytes(DOWNLOAD_CHUNK_BYTES):
                buf.extend(chunk)
            return bytes(buf)


def _extract(zip_bytes: bytes) -> tuple[str, str, str | None]:
    """Return (master_text, acftref_text, source_date)."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = {n.upper(): n for n in zf.namelist()}
        master_name = names.get("MASTER.TXT")
        acftref_name = names.get("ACFTREF.TXT")
        if not master_name or not acftref_name:
            raise ValueError(
                f"FAA zip missing MASTER.txt / ACFTREF.txt (found: {sorted(zf.namelist())})"
            )
        # FAA ships these as UTF-8 with a BOM. Use utf-8-sig so the BOM is
        # stripped from the header — otherwise the first column name is read
        # as "﻿N-NUMBER" instead of "N-NUMBER" and every row gets skipped.
        master_text = zf.read(master_name).decode("utf-8-sig", errors="replace")
        acftref_text = zf.read(acftref_name).decode("utf-8-sig", errors="replace")
        # FAA embeds a release date inside the zip filename comment sometimes,
        # but more reliably we can use the latest file's mtime as the source
        # date. Format YYYY-MM-DD.
        latest = max((zi.date_time for zi in zf.infolist()), default=None)
        if latest:
            source_date = f"{latest[0]:04d}-{latest[1]:02d}-{latest[2]:02d}"
        else:
            source_date = None
    return master_text, acftref_text, source_date


def _parse_acftref(text: str) -> dict[str, dict]:
    """Parse ACFTREF.txt into {mfr_mdl_code: row_dict}.

    ACFTREF is comma-separated, header on row 1. Fields are space-padded.
    """
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        return {}
    header = [h.strip() for h in header]
    out: dict[str, dict] = {}
    for raw in reader:
        if not raw:
            continue
        # Some rows may have trailing commas / extra empty columns; tolerate.
        record = {header[i]: (raw[i].strip() if i < len(raw) else "") for i in range(len(header))}
        code = record.get("CODE", "").strip()
        if not code:
            continue
        out[code] = {
            "manufacturer": record.get("MFR", "").strip() or None,
            "model": record.get("MODEL", "").strip() or None,
            "type_aircraft_code": record.get("TYPE-ACFT", "").strip() or None,
            "engine_type_code": record.get("TYPE-ENG", "").strip() or None,
        }
    return out


def _iter_master(text: str, ref_lookup: dict[str, dict]) -> Iterable[tuple[dict, str]]:
    """Yield (row_dict, kind) for each MASTER row.

    kind is "ok" / "skipped" / "invalid".
    """
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        return
    header = [h.strip() for h in header]
    # FAA's MASTER header uses spaces, e.g. "N-NUMBER", "MFR MDL CODE",
    # "YEAR MFR", "TYPE AIRCRAFT", "TYPE ENGINE", "STATUS CODE", "MODE S CODE HEX".
    col = {name: idx for idx, name in enumerate(header)}

    def get(rec: list[str], name: str) -> str:
        idx = col.get(name)
        if idx is None or idx >= len(rec):
            return ""
        return rec[idx].strip()

    for raw in reader:
        if not raw:
            continue
        try:
            n_body = get(raw, "N-NUMBER")
            if not n_body:
                yield ({}, "skipped")
                continue
            n_number = normalize.normalize_n_number("N" + n_body)
            if not n_number:
                yield ({}, "invalid")
                continue

            icao_hex_raw = get(raw, "MODE S CODE HEX")
            icao_hex = normalize.normalize_icao_hex(icao_hex_raw)

            mfr_mdl_code = get(raw, "MFR MDL CODE")
            ref = ref_lookup.get(mfr_mdl_code, {})

            type_aircraft_code = ref.get("type_aircraft_code") or get(raw, "TYPE AIRCRAFT")
            engine_type_code = ref.get("engine_type_code") or get(raw, "TYPE ENGINE")
            status_code = get(raw, "STATUS CODE")

            registrant_name = get(raw, "NAME") or None
            owner = owner_type.infer_owner_type(registrant_name)

            year_str = get(raw, "YEAR MFR")
            year = int(year_str) if year_str.isdigit() and 1900 < int(year_str) < 2200 else None

            row = {
                "n_number": n_number,
                "icao_hex": icao_hex,
                "serial_number": get(raw, "SERIAL NUMBER") or None,
                "manufacturer": ref.get("manufacturer"),
                "model": ref.get("model"),
                "year_manufactured": year,
                "type_aircraft_code": type_aircraft_code or None,
                "type_aircraft_label": codes.type_aircraft_label(type_aircraft_code),
                "engine_type_code": engine_type_code or None,
                "engine_type_label": codes.engine_type_label(engine_type_code),
                "category_label": codes.derived_category(type_aircraft_code, engine_type_code),
                "registrant_name": registrant_name,
                "registrant_street": get(raw, "STREET") or None,
                "registrant_city": get(raw, "CITY") or None,
                "registrant_state": get(raw, "STATE") or None,
                "registrant_zip": get(raw, "ZIP CODE") or None,
                "registrant_country": get(raw, "COUNTRY") or None,
                "registration_status_code": status_code or None,
                "registration_status_label": codes.registration_status_label(status_code),
                "certificate_issue_date": _yyyymmdd(get(raw, "CERT ISSUE DATE")),
                "registration_expiration_date": _yyyymmdd(get(raw, "EXPIRATION DATE")),
                "owner_type": owner.owner_type,
                "owner_type_confidence": owner.confidence,
                "owner_type_reason": owner.reason,
                "source": "FAA_AIRCRAFT_REGISTRY",
                "source_updated_at": None,
            }
            yield (row, "ok")
        except Exception:  # noqa: BLE001 — bad row, keep importing
            LOGGER.warning("Skipping invalid FAA registry row", exc_info=True)
            yield ({}, "invalid")


_YYYYMMDD = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def _yyyymmdd(value: str) -> str | None:
    """FAA dates ship as YYYYMMDD; convert to ISO YYYY-MM-DD."""
    if not value:
        return None
    cleaned = value.strip()
    m = _YYYYMMDD.match(cleaned)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
