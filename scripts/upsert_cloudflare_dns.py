#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


API_BASE = "https://api.cloudflare.com/client/v4"
COMMENT = "Managed by FAA_circle_jerk deployment"


def api_request(token: str, method: str, path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Cloudflare API {method} {path} failed: {exc.code} {detail}") from exc
    if not data.get("success"):
        raise RuntimeError(f"Cloudflare API {method} {path} failed: {data.get('errors')}")
    return data


def zone_id_for_domain(token: str, domain: str) -> str:
    query = urllib.parse.urlencode({"name": domain})
    data = api_request(token, "GET", f"/zones?{query}")
    zones = data.get("result") or []
    if not zones:
        raise RuntimeError(f"Cloudflare zone not found for {domain}")
    return zones[0]["id"]


def find_record(token: str, zone_id: str, record_type: str, name: str) -> dict | None:
    query = urllib.parse.urlencode({"type": record_type, "name": name, "per_page": 100})
    data = api_request(token, "GET", f"/zones/{zone_id}/dns_records?{query}")
    records = data.get("result") or []
    return records[0] if records else None


def upsert_record(
    token: str,
    zone_id: str,
    record_type: str,
    name: str,
    content: str,
    proxied: bool,
) -> str:
    payload = {
        "type": record_type,
        "name": name,
        "content": content,
        "ttl": 1,
        "proxied": proxied,
        "comment": COMMENT,
    }
    current = find_record(token, zone_id, record_type, name)
    if current is None:
        api_request(token, "POST", f"/zones/{zone_id}/dns_records", payload)
        return "created"

    needs_update = (
        current.get("content") != content
        or bool(current.get("proxied")) != proxied
        or current.get("comment") != COMMENT
    )
    if not needs_update:
        return "unchanged"

    api_request(token, "PUT", f"/zones/{zone_id}/dns_records/{current['id']}", payload)
    return "updated"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or update Cloudflare DNS records for production.")
    parser.add_argument("--domain", required=True, help="Apex domain, for example circlejerks.live")
    parser.add_argument("--origin-ip", required=True, help="Reserved origin IPv4 address")
    parser.add_argument("--unproxied", action="store_true", help="Disable Cloudflare proxying")
    args = parser.parse_args()

    token = os.environ.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CLOUDFLARE_API_KEY")
    if not token:
        print("CLOUDFLARE_API_TOKEN is required", file=sys.stderr)
        return 2

    domain = args.domain.strip().rstrip(".")
    proxied = not args.unproxied
    zone_id = zone_id_for_domain(token, domain)
    root_status = upsert_record(token, zone_id, "A", domain, args.origin_ip, proxied)
    www_status = upsert_record(token, zone_id, "CNAME", f"www.{domain}", domain, proxied)
    print(f"A {domain} -> {args.origin_ip} {root_status}")
    print(f"CNAME www.{domain} -> {domain} {www_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
