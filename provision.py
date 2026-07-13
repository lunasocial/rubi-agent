#!/usr/bin/env python3
"""Provision a tenant from a JSON file , onboarding is data, not code.

    python3 provision.py acme-cafe.json          # create/update tenants/{slug}
    python3 provision.py --show acme-cafe        # print a provisioned tenant
    python3 provision.py --example > new.json    # starter config to fill in

The JSON is the same shape as a businesses.py entry plus: slug, type (restaurant|cafe|hotel|retail),
and optionally line (the tenant's dedicated phone number). Requires Firestore (demo seeds don't).
After provisioning, point the tenant's Linq number webhook at {BASE}/webhook/{slug}.
"""
import json
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    pass

import businesses
import store

BASE = os.getenv("RUBI_PUBLIC_BASE", "https://agent.contextualintelligence.co/rubi")

REQUIRED = ("slug", "name", "type", "neighborhood", "address", "known_for",
            "reservation_policy", "takeout", "hours", "menu")

EXAMPLE = {
    "slug": "acme-cafe", "name": "Acme Cafe", "type": "cafe", "cuisine": "specialty coffee",
    "neighborhood": "SoHo, Manhattan", "address": "1 Example St, New York, NY 10001",
    "line": "+1XXXXXXXXXX",
    "known_for": "single-origin espresso and fresh pastries",
    "reservation_policy": "No reservations , walk-ins and pickup orders.",
    "takeout": "Yes, pickup and delivery.",
    "hours": {"Monday": "7:00 AM - 6:00 PM", "Tuesday": "7:00 AM - 6:00 PM",
              "Wednesday": "7:00 AM - 6:00 PM", "Thursday": "7:00 AM - 6:00 PM",
              "Friday": "7:00 AM - 6:00 PM", "Saturday": "8:00 AM - 5:00 PM",
              "Sunday": "8:00 AM - 5:00 PM"},
    "menu": {"Coffee": ["Espresso", "Cortado", "Latte"], "Pastries": ["Croissant", "Banana bread"]},
    "owner_phone": "",
}


def provision(cfg: dict) -> None:
    missing = [f for f in REQUIRED if not cfg.get(f)]
    if missing:
        sys.exit(f"missing required fields: {', '.join(missing)}")
    if cfg["type"] not in businesses.TEMPLATES:
        sys.exit(f"type must be one of: {', '.join(businesses.TEMPLATES)}")
    slug = cfg.pop("slug").strip().lower()
    db = store.db()
    if db is None:
        sys.exit("Firestore required to provision tenants (set GOOGLE_APPLICATION_CREDENTIALS).")
    db.collection("tenants").document(slug).set(cfg, merge=True)
    businesses.invalidate(slug)
    print(f"\nProvisioned {cfg['name']}  ({slug}, {cfg['type']})")
    print(f"  dashboard : {BASE}/{slug}/")
    print(f"  webhook   : {BASE}/webhook/{slug}")
    if cfg.get("line"):
        print(f"  line      : {cfg['line']}")
        print("  next      : import this number into Linq (messaging only , voice stays with their")
        print("              carrier, staff keep answering calls) and point its webhook at the URL above.")
    else:
        print("  note      : no line yet , import the business's existing number into Linq (messaging")
        print("              only), set `line`, and point its webhook at the URL above. Until then the")
        print("              shared demo line reaches this tenant via switch.py.")
    print()


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__.strip())
    if sys.argv[1] == "--example":
        print(json.dumps(EXAMPLE, indent=2))
        return
    if sys.argv[1] == "--show":
        cfg = businesses._fs_tenant(sys.argv[2]) if len(sys.argv) > 2 else None
        print(json.dumps(cfg, indent=2, default=str) if cfg else "not found")
        return
    with open(sys.argv[1]) as f:
        provision(json.load(f))


if __name__ == "__main__":
    main()
