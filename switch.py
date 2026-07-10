#!/usr/bin/env python3
"""Switch which business the phone line (+1 904-874-1368) currently serves.

    python3 switch.py                # show current + all options
    python3 switch.py giorgios       # point the line at Giorgio's

Takes effect immediately , the server reads the active slug per incoming text, no restart needed.
Every business's dashboard stays live at its own URL regardless of which one owns the line.
"""
import os
import sys

import businesses

BASE = os.getenv("RUBI_PUBLIC_BASE", "https://agent.contextualintelligence.co/rubi")
ACTIVE_FILE = os.path.join(os.path.dirname(__file__), "active_business.txt")


def show():
    active = businesses.active_slug()
    print(f"\nPhone line currently serves:  {businesses.get(active)['name']}  ({active})\n")
    print("Businesses:")
    for slug, cfg in businesses.BUSINESSES.items():
        mark = "  <- LIVE ON THE LINE" if slug == active else ""
        print(f"  {slug:14s} {cfg['name']:26s} {BASE}/{slug}/{mark}")
    print(f"\nSwitch with:  python3 switch.py <slug>\n")


def main():
    if len(sys.argv) < 2:
        show()
        return
    slug = sys.argv[1].strip().lower()
    if slug not in businesses.BUSINESSES:
        print(f"Unknown business '{slug}'. Options: {', '.join(businesses.BUSINESSES)}")
        sys.exit(1)
    with open(ACTIVE_FILE, "w") as f:
        f.write(slug)
    cfg = businesses.get(slug)
    print(f"\nThe line now serves {cfg['name']}.")
    print(f"Dashboard: {BASE}/{slug}/\n")


if __name__ == "__main__":
    main()
