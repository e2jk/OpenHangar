#!/usr/bin/env python3
"""
Generate a structured inventory of all Flask routes for E2E testing.

Connects to the database, substitutes real IDs into URL params from live data,
and writes tests/e2e/routes.json.

Each record:
  {
    "endpoint":      "aircraft.detail",
    "blueprint":     "aircraft",
    "rule":          "/aircraft/<int:aircraft_id>",
    "method":        "GET",
    "url":           "/aircraft/1",          # null if params unresolvable
    "params":        {"aircraft_id": 1},
    "auth_required": true,
    "notes":         null
  }

Usage:
    python scripts/generate_routes.py [--db-url URL] [--base-url URL] [--out PATH]

OPENHANGAR_DATABASE_URL env var is used as default db-url when the flag is omitted.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "app"))

# ── Known public endpoints (no login required) ───────────────────────────────
PUBLIC_ENDPOINTS = {
    "static",
    "auth.login",
    "auth.logout",
    "auth.setup",
    "auth.reset_password",
    "share.public_share",
    "pilots.set_language",  # /set-language/<lang>
    "squawk.squawk",  # /squawk/<code>
    "squawk.squawk_1200",
    "squawk.squawk_7000",
    "squawk.squawk_7500",
    "squawk.squawk_7600",
    "squawk.squawk_7700",
    "hangar.secret",
    "health",
    "robots",
    "favicon",
    "config.invite_accept",  # /config/users/invite/<token>
}

# ── Static/infra routes to omit entirely from the output ─────────────────────
SKIP_ENDPOINTS = {
    "static",
    "favicon",
    "robots",
}

SKIP_RULES = {
    "/static/<path:filename>",
    "/favicon.ico",
    "/robots.txt",
    "/uploads/<path:filename>",
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--db-url",
        default=os.environ.get("OPENHANGAR_DATABASE_URL"),
        help="SQLAlchemy DB URL (defaults to $OPENHANGAR_DATABASE_URL)",
    )
    p.add_argument(
        "--base-url",
        default="",
        help="Optional base URL prefix (e.g. https://openhangar.example.com)",
    )
    p.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "tests" / "e2e" / "routes.json"),
        help="Output file path",
    )
    p.add_argument(
        "--seed-out",
        default=None,
        metavar="PATH",
        help="If set, write _query_samples() result as JSON to this path (used by E2E conftest in Docker mode)",
    )
    return p.parse_args()


def _build_app(db_url: str):
    os.environ.setdefault("OPENHANGAR_SECRET_KEY", "a" * 64)
    os.environ["OPENHANGAR_DATABASE_URL"] = db_url
    from init import create_app  # pyright: ignore[reportMissingImports]

    return create_app()


def _query_samples(app) -> dict:
    """Return a dict of param_name → sample value drawn from live DB data."""
    from models import (  # pyright: ignore[reportMissingImports]
        Aircraft,
        AircraftGpsImportBatch,
        AircraftPhoto,
        Component,
        Document,
        Expense,
        FlightEntry,
        LogbookImportBatch,
        MaintenanceTrigger,
        PendingReconcile,
        PilotLogbookEntry,
        Reservation,
        ShareToken,
        Snag,
        Tenant,
        User,
        UserInvitation,
        WeightBalanceEntry,
    )

    with app.app_context():
        ac_list = Aircraft.query.order_by(Aircraft.id).limit(4).all()
        ac = ac_list[0] if ac_list else None
        ac2 = ac_list[1] if len(ac_list) > 1 else None  # seminole
        ac3 = ac_list[2] if len(ac_list) > 2 else None  # robin
        ac4 = ac_list[3] if len(ac_list) > 3 else None  # jodel
        ac_id = ac.id if ac else None

        comp = Component.query.filter_by(aircraft_id=ac_id).first() if ac_id else None
        photo = (
            AircraftPhoto.query.filter_by(aircraft_id=ac_id).first() if ac_id else None
        )
        doc_ac = Document.query.filter_by(aircraft_id=ac_id).first() if ac_id else None
        # Most-recent flights match what appears at the top of each date-desc list
        flight = (
            FlightEntry.query.filter_by(aircraft_id=ac_id)
            .order_by(FlightEntry.date.desc())
            .first()
            if ac_id
            else None
        )
        # Extra flights for delete/action-cell tests in Docker E2E mode
        flight2 = (
            FlightEntry.query.filter_by(aircraft_id=ac2.id)
            .order_by(FlightEntry.date.desc())
            .first()
            if ac2
            else None
        )
        flight3 = (
            FlightEntry.query.filter_by(aircraft_id=ac3.id)
            .order_by(FlightEntry.date.desc())
            .first()
            if ac3
            else None
        )
        # First flight of the 4th aircraft (jodel) — used for duplicate-detection test
        flight4 = (
            FlightEntry.query.filter_by(aircraft_id=ac4.id)
            .order_by(FlightEntry.date)
            .first()
            if ac4
            else None
        )
        expense = Expense.query.filter_by(aircraft_id=ac_id).first() if ac_id else None
        snag = Snag.query.filter_by(aircraft_id=ac_id).first() if ac_id else None
        trigger = (
            MaintenanceTrigger.query.filter_by(aircraft_id=ac_id).first()
            if ac_id
            else None
        )
        from models import WeightBalanceConfig  # pyright: ignore[reportMissingImports]

        wb_cfg = (
            WeightBalanceConfig.query.filter_by(aircraft_id=ac_id).first()
            if ac_id
            else None
        )
        wb = (
            WeightBalanceEntry.query.filter_by(config_id=wb_cfg.id).first()
            if wb_cfg
            else None
        )
        res = Reservation.query.filter_by(aircraft_id=ac_id).first() if ac_id else None
        share_token = (
            ShareToken.query.filter_by(aircraft_id=ac_id).first() if ac_id else None
        )
        gps_batch = AircraftGpsImportBatch.query.first()
        logbook_batch = LogbookImportBatch.query.first()
        tenant = Tenant.query.first()
        user = User.query.first()
        pilot_entry = PilotLogbookEntry.query.first()
        pilot_doc = Document.query.filter(Document.pilot_user_id.isnot(None)).first()
        pending = PendingReconcile.query.first()
        inv = UserInvitation.query.first()

        return {
            "aircraft_id": ac_id,
            # Extra aircraft IDs for Docker E2E mode (ac2=seminole, ac3=robin, ac4=jodel)
            "aircraft_id_2": ac2.id if ac2 else None,
            "aircraft_id_3": ac3.id if ac3 else None,
            "aircraft_id_4": ac4.id if ac4 else None,
            "component_id": comp.id if comp else None,
            "photo_id": photo.id if photo else None,
            "document_id_ac": doc_ac.id if doc_ac else None,
            "document_id_pilot": pilot_doc.id if pilot_doc else None,
            "flight_id": flight.id if flight else None,
            # Extra flight IDs for delete/action-cell tests in Docker E2E mode
            "flight_id_2": flight2.id if flight2 else None,
            "flight_id_3": flight3.id if flight3 else None,
            # Duplicate-detection anchor: a real existing jodel flight
            "dup_date": flight4.date.isoformat() if flight4 else None,
            "dup_dep": flight4.departure_icao if flight4 else None,
            "dup_arr": flight4.arrival_icao if flight4 else None,
            "expense_id": expense.id if expense else None,
            "snag_id": snag.id if snag else None,
            "trigger_id": trigger.id if trigger else None,
            "wb_entry_id": wb.id if wb else None,
            "res_id": res.id if res else None,
            "token_id": share_token.id if share_token else None,
            "gps_batch_id": gps_batch.id if gps_batch else None,
            "logbook_batch_id": logbook_batch.id if logbook_batch else None,
            "tenant_id": tenant.id if tenant else None,
            "user_id": user.id if user else None,
            "pilot_entry_id": pilot_entry.id if pilot_entry else None,
            "pending_id": pending.id if pending else None,
            "inv_id": inv.id if inv else None,
            # Literal values for non-int params
            "lang": "fr",
            "code": 7700,
            "token": None,  # invite/reset tokens can't be queried safely
        }


def _resolve_params(
    rule_str: str, arguments: set, samples: dict
) -> tuple[dict, list[str]]:
    """
    Map URL arguments to sample values.
    Returns (params_dict, unresolved_list).
    """
    resolved = {}
    unresolved = []

    for arg in arguments:
        value = None

        if arg == "aircraft_id":
            value = samples["aircraft_id"]
        elif arg == "component_id":
            value = samples["component_id"]
        elif arg == "photo_id":
            value = samples["photo_id"]
        elif arg == "document_id":
            if "/pilot/" in rule_str:
                value = samples["document_id_pilot"]
            else:
                value = samples["document_id_ac"]
        elif arg == "flight_id":
            value = samples["flight_id"]
        elif arg == "expense_id":
            value = samples["expense_id"]
        elif arg == "snag_id":
            value = samples["snag_id"]
        elif arg == "trigger_id":
            value = samples["trigger_id"]
        elif arg == "service_id":
            value = samples["service_id"]
        elif arg == "entry_id":
            if "/wb/" in rule_str:
                value = samples["wb_entry_id"]
            elif "/pilot/logbook/" in rule_str:
                value = samples["pilot_entry_id"]
            else:
                value = None
        elif arg == "res_id":
            value = samples["res_id"]
        elif arg == "token_id":
            value = samples["token_id"]
        elif arg == "batch_id":
            if "/gps-import/" in rule_str:
                value = samples["gps_batch_id"]
            else:
                value = samples["logbook_batch_id"]
        elif arg == "tenant_id":
            value = samples["tenant_id"]
        elif arg == "user_id":
            value = samples["user_id"]
        elif arg == "pending_id":
            value = samples["pending_id"]
        elif arg == "inv_id":
            value = samples["inv_id"]
        elif arg == "lang":
            value = samples["lang"]
        elif arg == "code":
            value = samples["code"]
        elif arg == "token":
            value = samples["token"]
        elif arg == "filename":
            value = "example.pdf"

        if value is None:
            unresolved.append(arg)
        else:
            resolved[arg] = value

    return resolved, unresolved


def _is_auth_required(endpoint: str) -> bool:
    return endpoint not in PUBLIC_ENDPOINTS


def generate(
    db_url: str, base_url: str, out_path: str, seed_out: str | None = None
) -> None:
    print(
        f"Connecting to {db_url[: db_url.index('@') + 1 if '@' in db_url else len(db_url)]}…"
    )
    app = _build_app(db_url)

    with app.app_context():
        samples = _query_samples(app)

    if seed_out:
        seed_path = Path(seed_out)
        seed_path.parent.mkdir(parents=True, exist_ok=True)
        seed_path.write_text(json.dumps(samples, indent=2))
        print(f"Seed data written → {seed_path}")

    routes = []

    with app.test_request_context():
        from flask import url_for  # pyright: ignore[reportMissingImports]

        for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
            if rule.rule in SKIP_RULES:
                continue
            endpoint = rule.endpoint
            if endpoint in SKIP_ENDPOINTS:
                continue

            blueprint = endpoint.rsplit(".", 1)[0] if "." in endpoint else None
            methods = sorted(rule.methods - {"HEAD", "OPTIONS"})

            params, unresolved = _resolve_params(rule.rule, rule.arguments, samples)

            # Try to build the URL
            url = None
            notes = None
            if not unresolved:
                try:
                    url = base_url + url_for(endpoint, **params)
                except Exception as exc:
                    notes = f"url_for failed: {exc}"
            else:
                notes = f"params unresolvable in seed DB: {unresolved}"

            for method in methods:
                routes.append(
                    {
                        "endpoint": endpoint,
                        "blueprint": blueprint,
                        "rule": rule.rule,
                        "method": method,
                        "url": url,
                        "params": params if params else None,
                        "auth_required": _is_auth_required(endpoint),
                        "notes": notes,
                    }
                )

    # Summary counts
    total = len(routes)
    buildable = sum(1 for r in routes if r["url"])
    get_count = sum(1 for r in routes if r["method"] == "GET")
    post_count = sum(1 for r in routes if r["method"] == "POST")

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_url_masked": db_url[: db_url.index("@") + 1] + "…"
        if "@" in db_url
        else db_url,
        "summary": {
            "total": total,
            "buildable": buildable,
            "get": get_count,
            "post": post_count,
            "other": total - get_count - post_count,
        },
        "routes": routes,
    }

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, indent=2))

    print(f"Written {total} routes ({buildable} with resolved URLs) → {out}")
    print(
        f"  GET: {get_count}  POST: {post_count}  other: {total - get_count - post_count}"
    )
    unbuilt = [r for r in routes if not r["url"]]
    if unbuilt:
        print(f"  {len(unbuilt)} routes without a resolvable URL:")
        for r in unbuilt:
            print(f"    {r['method']:<8} {r['rule']}  ({r['notes']})")


if __name__ == "__main__":
    args = _parse_args()
    if not args.db_url:
        print("ERROR: --db-url or $OPENHANGAR_DATABASE_URL required", file=sys.stderr)
        sys.exit(1)
    generate(args.db_url, args.base_url, args.out, args.seed_out)
