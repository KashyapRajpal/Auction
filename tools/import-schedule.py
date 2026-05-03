"""
Convert the GovEase public auction-schedule table into data/auction-schedule.json.

Source: https://www.govease.com/tax-sale-property-auctions (Awesome Table widget,
not exposed as JSON). Paste the table as plain TSV-style rows below. Run:

  python tools/import-schedule.py

Re-run whenever the public schedule page changes (~quarterly).
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pasted from https://www.govease.com/tax-sale-property-auctions on 2026-05-01.
# Empty values (Pre-Bidding / time slots) become None.
RAW = """\
Autauga|AL|03/13/2026|04/13/2026|8:30:00 AM|04/14/2026|10:00:00 AM|Lien Sale|Prattville
Dale|AL|03/13/2026|04/13/2026|8:30:00 AM|04/14/2026|8:30:00 AM|Lien Sale|Ozark
Henry County|AL|03/09/2026|04/13/2026|10:00:00 AM|04/14/2026|10:00:00 AM|Lien Sale|Abbeville
Elmore|AL|03/16/2026|04/15/2026|8:00:00 AM|04/16/2026|9:00:00 AM|Lien Sale|Wetumpka
Butler|AL|03/18/2026|04/16/2026|8:30:00 AM|04/17/2026|10:00:00 AM|Lien Sale|Greenville
Los Angeles|CA|03/13/2026|||04/18/2026|5:00:00 PM|Deed Sale|Los Angeles
Colbert|AL|03/20/2026|04/19/2026|8:30:00 AM|04/20/2026|10:00:00 AM|Lien Sale|Tuscumbia
Polk|IA|04/09/2026|04/16/2026|8:00:00 AM|04/20/2026|8:00:00 AM|Lien Sale|Des Moines
Randolph|AL|03/20/2026|04/20/2026|8:30:00 AM|04/21/2026|8:30:00 AM|Lien Sale|Wedowee
DeKalb|AL|03/23/2026|04/21/2026|8:30:00 AM|04/22/2026|10:00:00 AM|Lien Sale|Fort Payne
Lawrence Judicial Sale|PA|04/06/2026||7:00:00 AM|04/23/2026|9:00:00 AM|Deed Sale|New Castle
Carroll|TN|03/24/2026|04/23/2026|8:30:00 AM|04/24/2026|10:00:00 AM|Redeemable Deed Sale|Huntingdon
Jackson|AL|03/27/2026|04/27/2026|8:30:00 AM|04/28/2026|10:00:00 AM|Lien Sale|Scottsboro
Tuscaloosa|AL|03/26/2026|04/28/2026|8:30:00 AM|04/29/2026|9:00:00 AM|Lien Sale|Tuscaloosa
Cherokee|AL|03/30/2026|04/29/2026|8:30:00 AM|04/30/2026|10:00:00 AM|Lien Sale|Centre
Etowah|AL|04/03/2026|05/01/2026|9:00:00 AM|05/04/2026|9:00:00 AM|Lien Sale|Gadsden
Houston|AL|04/03/2026|05/02/2026|9:00:00 AM|05/04/2026|9:00:00 AM|Lien Sale|Dothan
Monroe|AL|04/06/2026|05/01/2026|8:30:00 AM|05/04/2026|10:00:00 AM|Lien Sale|Monroeville
Walker|AL|04/03/2026|05/02/2026|8:30:00 AM|05/04/2026|8:30:00 AM|Lien Sale|Jasper
Barbour|AL|04/03/2026|05/04/2026|10:00:00 AM|05/05/2026|11:00:00 AM|Lien Sale|Eufaula
Bibb|AL|04/03/2026|05/04/2026|8:30:00 AM|05/05/2026|10:00:00 AM|Lien Sale|Centreville
Calhoun|AL|03/31/2026|05/01/2026|8:30:00 AM|05/05/2026|8:30:00 AM|Lien Sale|Anniston
Franklin|AL|04/03/2026|05/04/2026|10:00:00 AM|05/05/2026|10:00:00 AM|Lien Sale|Russellville
Jefferson(Bessemer)|AL|04/01/2026|05/04/2026|8:30:00 AM|05/05/2026|8:30:00 AM|Lien Sale|Birmingham
Lamar|AL|04/06/2026|05/04/2026|8:30:00 AM|05/05/2026|10:00:00 AM|Lien Sale|Vernon
Madison|AL|04/03/2026|05/04/2026|9:00:00 AM|05/05/2026|9:00:00 AM|Lien Sale|Huntsville
Marengo|AL|04/02/2026|05/04/2026|8:30:00 AM|05/05/2026|8:30:00 AM|Lien Sale|Linden
St. Clair|AL|04/01/2026|05/01/2026|8:30:00 AM|05/05/2026|9:00:00 AM|Tax Lien Auction|Ashville
Denton|TX|04/13/2026|||05/05/2026|10:00:00 AM|Redeemable Tax Deed Auction|Denton
Mclennan|TX|04/14/2026|||05/05/2026|2:00:00 PM|Redeemable Deed Sale|Waco
Winston|AL|04/06/2026|05/06/2026|8:30:00 AM|05/07/2026|10:00:00 AM|Lien Sale|Double Springs
Mobile|AL|04/06/2026|05/09/2026|8:30:00 AM|05/11/2026|8:30:00 AM|Lien Sale|Mobile
Dallas|AL|04/10/2026|05/11/2026|8:30:00 AM|05/12/2026|9:00:00 AM|Lien Sale|Selma
Macon|AL|04/10/2026|05/11/2026|8:30:00 AM|05/12/2026|10:00:00 AM|Lien Sale|Tuskegee
Morgan|AL|04/10/2026|05/11/2026|8:30:00 AM|05/12/2026|10:00:00 AM|Lien Sale|Decatur
Talladega|AL|04/27/2026|05/08/2026|8:30:00 AM|05/12/2026|9:00:00 AM|Lien Sale|Talladega
Del Norte|CA|04/15/2026|05/14/2026|11:00:00 AM|05/15/2026|11:00:00 AM|Deed Sale|Crescent City
Marion Sheriff's Sale|IN||||05/15/2026|9:30:00 AM|Deed Sale|Indianapolis
Covington|AL|04/17/2026|05/15/2026|9:00:00 AM|05/18/2026|10:00:00 AM|Lien Sale|Andalusia
Marion|AL|04/17/2026|05/15/2026|9:00:00 AM|05/18/2026|9:00:00 AM|Lien Sale|Hamilton
Escambia|AL|04/14/2026|05/18/2026|8:30:00 AM|05/19/2026|8:30:00 AM|Lien Sale|Brewton
Hale|AL|04/15/2026|05/15/2026|8:30:00 AM|05/19/2026|12:00:00 PM|Lien Sale|Greensboro
Obion|TN|04/30/2026|05/18/2026|10:00:00 AM|05/21/2026|10:00:00 AM|Redeemable Deed Sale|Union City
Pike|AL|05/06/2026|06/04/2026|9:00:00 AM|06/05/2026|9:00:00 AM|Lien Sale|Troy
"""

COLS = ['county_name', 'state', 'registration_date', 'pre_bidding_date',
        'pre_bidding_time_ct', 'sale_date', 'sale_time_ct', 'sale_type', 'city']


def parse_date(s: str) -> str | None:
    """MM/DD/YYYY -> YYYY-MM-DD."""
    s = s.strip()
    if not s:
        return None
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', s)
    if not m:
        return None
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"


def parse_time(s: str) -> str | None:
    """'8:30:00 AM' -> '08:30' (24h, dropping seconds since we don't need them)."""
    s = s.strip()
    if not s:
        return None
    m = re.match(r'^(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)?$', s, re.I)
    if not m:
        return None
    h, mm, ampm = int(m.group(1)), m.group(2), (m.group(3) or '').upper()
    if ampm == 'PM' and h < 12:
        h += 12
    elif ampm == 'AM' and h == 12:
        h = 0
    return f"{h:02d}:{mm}"


def normalize(s: str) -> str:
    return (s.lower()
             .replace(' county', '')
             .replace("'", '')
             .replace('(', ' ').replace(')', '')
             .strip())


def match_county(state: str, raw_name: str, counties: list[dict]) -> dict | None:
    n = normalize(raw_name)
    # 1. exact normalized match
    for c in counties:
        if c['state'] == state and normalize(c['name']) == n:
            return c
    # 2. first-token starts-with — handles 'Lawrence Judicial Sale' -> 'Lawrence - Judicial Sale'
    first = n.split()[0]
    for c in counties:
        if c['state'] == state and normalize(c['name']).startswith(first):
            return c
    return None


def main() -> int:
    counties = json.load(open(os.path.join(ROOT, 'data', 'counties.json')))['counties']

    auctions: list[dict] = []
    unmatched: list[str] = []
    for line in RAW.strip().splitlines():
        parts = line.split('|')
        if len(parts) != len(COLS):
            print(f"skipping malformed line: {line!r}", file=sys.stderr)
            continue
        rec = dict(zip(COLS, [p.strip() for p in parts]))
        c = match_county(rec['state'], rec['county_name'], counties)
        entry = {
            'county_id':           c['id'] if c else None,
            'county_label':        c['label'] if c else None,
            'state':               rec['state'],
            'county_name':         rec['county_name'],
            'city':                rec['city'] or None,
            'registration_date':   parse_date(rec['registration_date']),
            'pre_bidding_date':    parse_date(rec['pre_bidding_date']),
            'pre_bidding_time_ct': parse_time(rec['pre_bidding_time_ct']),
            'sale_date':           parse_date(rec['sale_date']),
            'sale_time_ct':        parse_time(rec['sale_time_ct']),
            'sale_type':           rec['sale_type'],
        }
        if not c:
            unmatched.append(f"{rec['state']} {rec['county_name']}")
        auctions.append(entry)

    # Sort soonest sale first
    auctions.sort(key=lambda a: (a['sale_date'] or '9999', a['state'], a['county_name']))

    payload = {
        'updated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'source':     'https://www.govease.com/tax-sale-property-auctions',
        'note':       'Pasted from the public Awesome Table widget; refresh quarterly.',
        'count':      len(auctions),
        'matched':    sum(1 for a in auctions if a['county_id']),
        'auctions':   auctions,
    }
    out = os.path.join(ROOT, 'data', 'auction-schedule.json')
    with open(out, 'w') as f:
        json.dump(payload, f, indent=2)
    print(f"✓ wrote {len(auctions)} entries → {out}")
    print(f"  matched to a counties.json entry: {payload['matched']}/{len(auctions)}")
    if unmatched:
        print(f"  unmatched (not in counties.json — schedule-only): {unmatched}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
