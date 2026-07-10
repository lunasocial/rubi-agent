#!/usr/bin/env python3
"""Verify live availability end to end. Run on the box after setting RUBI_AVAIL_PROXY in .env.

    python3 test_availability.py                 # today, party of 2, all businesses
    python3 test_availability.py 2026-07-17 2    # specific date + party size

Prints real Resy/OpenTable openings per business, or a clear reason if none came back.
"""
import asyncio
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    pass

import availability
import businesses


async def main():
    day = sys.argv[1] if len(sys.argv) > 1 else None
    party = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    if not day:
        import datetime
        try:
            from zoneinfo import ZoneInfo
            day = datetime.datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        except Exception:
            day = datetime.date.today().isoformat()

    print(f"proxy set: {bool(os.getenv('RUBI_AVAIL_PROXY') or os.getenv('AVAIL_PROXY_URL') or os.getenv('RESY_PROXY'))}  "
          f"scrapfly set: {bool(os.getenv('SCRAPFLY_API_KEY'))}")
    print(f"checking {day} for party of {party}\n")
    for slug, cfg in businesses.BUSINESSES.items():
        if not ((cfg.get('resy') or {}).get('venue_id') or (cfg.get('opentable') or {}).get('rid')):
            print(f"{cfg['name']:26s}  no reservation platform configured (skipped)")
            continue
        res = await availability.check(cfg, day, party, "19:00")
        if not res:
            print(f"{cfg['name']:26s}  lookup failed (proxy/edge). check RUBI_AVAIL_PROXY.")
        elif not res['all_slots']:
            print(f"{cfg['name']:26s}  no online tables ({res['platform']})")
        else:
            print(f"{cfg['name']:26s}  {res['platform']}: {', '.join(res['all_slots'][:12])}")


if __name__ == "__main__":
    asyncio.run(main())
