"""
GovEase HTTP client + HTML/JSON parsers.

Three things we pull from GovEase:
  1. The state/county dropdown (any auction page renders the full list).
  2. The paginated AJAX list of parcels for one county.
  3. The per-parcel detail page (rich data) and the shape endpoint (lat/lng + polygon).
"""

from __future__ import annotations

import json
import random
import re
import time
from html import unescape
from typing import Any

import requests

# ── HTTP ──────────────────────────────────────────────────────────────────────
BASE = 'https://liveauctions.govease.com'

USER_AGENTS = [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
]

def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({'User-Agent': random.choice(USER_AGENTS)})
    return s

def polite_sleep(lo: float = 2.0, hi: float = 5.0) -> None:
    time.sleep(random.uniform(lo, hi))


# ── small helpers ─────────────────────────────────────────────────────────────
TAGS_RE = re.compile(r'<[^>]+>')

def strip_tags(s: str) -> str:
    return unescape(TAGS_RE.sub('', s)).strip()

def parse_money(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r'([\d,]+(?:\.\d+)?)', s.replace(' ', ''))
    return float(m.group(1).replace(',', '')) if m else None

def parse_int(s: str | None) -> int | None:
    if not s:
        return None
    m = re.search(r'\d+', s)
    return int(m.group(0)) if m else None


# ── counties dropdown ─────────────────────────────────────────────────────────
COUNTY_OPTION_RE = re.compile(
    r'<option value="(?P<state>[a-z]{2})\|(?P<slug>[^"|]+)\|(?P<id>\d+)"[^>]*>'
    r'(?P<label>[^<]+)</option>'
)

# Any auction page renders the full dropdown; the home page is filtered.
SEED_PAGE = '/AL/albarbour/1262/browsebiddown'

def fetch_counties(s: requests.Session) -> list[dict]:
    r = s.get(f'{BASE}{SEED_PAGE}', timeout=20)
    r.raise_for_status()
    seen: dict[int, dict] = {}
    for m in COUNTY_OPTION_RE.finditer(r.text):
        cid = int(m['id'])
        if cid in seen:
            continue
        label = unescape(m['label']).strip()
        name  = re.sub(r'^[A-Z]{2}\s*-\s*', '', label)
        seen[cid] = {
            'state':    m['state'].upper(),
            'slug':     m['slug'],
            'id':       cid,
            'name':     name,
            'label':    label,
            'snapshot': f"{m['state'].upper()}-{m['slug']}.json",
        }
    return sorted(seen.values(), key=lambda c: (c['state'], c['name']))


# ── list page (paginated AJAX) ────────────────────────────────────────────────
LIST_ENDPOINT = '/OpenAuction/RefreshBidDownAuctions'

ROW_RE  = re.compile(r'<tr role="row"[^>]*>(.*?)</tr>', re.DOTALL)
TD_RE   = re.compile(r'<td[^>]*>(.*?)</td>', re.DOTALL)
LINK_RE = re.compile(r'/openbidownparcel/(\d+)/([\w\-]+)')

def fetch_list_page(s: requests.Session, state: str, slug: str, county_id: int,
                    page: int, page_size: int = 50) -> str:
    body = {
        "countyId": county_id,
        "criteria": {
            "PostUrl":  f"/{state}/{slug}/{county_id}/browsebiddown",
            "ResetUrl": f"/{state}/{slug}/{county_id}/browsebiddown",
            "StateAbbr": state, "CountySlug": slug, "CountyID": county_id,
            "ParcelNumber": "", "CountyIdList": [], "Location": "",
            "OwnerName": "", "FaceValueFrom": None, "FaceValueTo": None,
            "WatchList": False, "AuctionLienFilter": None, "MultiSearchBox": None,
        },
        "pageNumber": page, "pageSize": page_size,
        "orderBy": "phys_file_name", "orderDesc": 0, "stateAbbr": state,
    }
    r = s.post(f'{BASE}{LIST_ENDPOINT}', json=body,
               headers={'X-Requested-With': 'XMLHttpRequest'}, timeout=30)
    r.raise_for_status()
    return r.json().get('Grid', '') or ''


def parse_list_grid(grid_html: str) -> list[dict]:
    """Columns: [ctl, watch, unique#-link, parcel#, owner, faceval, addr,
                 auction_name, auction_type, …]."""
    rows: list[dict] = []
    for tr in ROW_RE.finditer(grid_html):
        cells = TD_RE.findall(tr.group(1))
        if len(cells) < 7:
            continue
        link = LINK_RE.search(cells[2])
        if not link:
            continue
        owner_html = cells[4]
        owner_full = re.search(r'data-content="([^"]+)"', owner_html)
        owner = unescape(owner_full.group(1)).strip() if owner_full else strip_tags(owner_html)
        rows.append({
            'lot_id':        int(link.group(1)),
            'parcel_slug':   link.group(2),
            'parcel_number': strip_tags(cells[3]),
            'owner':         owner,
            'face_value':    parse_money(strip_tags(cells[5])),
            'list_address':  strip_tags(cells[6]) or None,
            'auction_name':  strip_tags(cells[7]) if len(cells) > 7 else None,
            'auction_type':  strip_tags(cells[8]) if len(cells) > 8 else None,
        })
    return rows


# ── parcel detail ─────────────────────────────────────────────────────────────
def fetch_detail(s: requests.Session, state: str, slug: str, county_id: int,
                 lot_id: int, parcel_slug: str) -> str:
    url = (f'{BASE}/{state.lower()}/{slug}/{county_id}/'
           f'openbidownparcel/{lot_id}/{parcel_slug}')
    r = s.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def _field(html: str, label: str) -> str | None:
    pat = (rf'<span class="fw-500">{re.escape(label)}</span>'
           r'.*?<span[^>]*>(.*?)</span>')
    m = re.search(pat, html, re.DOTALL)
    return strip_tags(m.group(1)) if m else None


def _legal_description(html: str) -> str | None:
    m = re.search(
        r'<span class="fw-500">Legal Description</span>.*?'
        r'<div[^>]*>(.*?)</div>',
        html, re.DOTALL)
    if not m:
        return None
    parts = re.findall(r'<span[^>]*>([^<]*)</span>', m.group(1))
    out = ' '.join(p.strip() for p in parts if p.strip())
    return out or None


# Each "row ml-0 p-2" block has the shape:
#   <div class="row ml-0 p-2">
#     <div><span class="fw-500">{label}</span></div>
#     <div class="ml-auto mr-0"><span ...>{value}</span></div>
#   </div>
# The mailing-address block is 6 consecutive rows whose values are:
#   street1, street2, street3, city, state, zip   (any of street2/3 may be empty)
ROW_VALUE_RE = re.compile(
    r'<div class="row[^"]*ml-0[^"]*">.*?'
    r'<div class="ml-auto[^"]*">\s*<span[^>]*>([^<]*)</span>',
    re.DOTALL,
)

_ROW_START_RE = re.compile(r'<div class="row[^"]*ml-0[^"]*">')

def _mailing_address(html: str) -> dict:
    """Walk the 6 row blocks starting with the 'Address' row and unpack them.
    Heuristic: zip is 5/9 digits, state is 2 letters; pop those from the tail."""
    addr = html.find('<span class="fw-500">Address</span>')
    if addr < 0:
        return {}
    # Anchor at the START of the row containing the Address label, so we capture
    # that row's value (street1) along with the 5 following rows.
    row_start = None
    for m in _ROW_START_RE.finditer(html):
        if m.start() > addr:
            break
        row_start = m.start()
    if row_start is None:
        return {}
    tail = html[row_start:]
    raw = [unescape(m.group(1)).strip() for m in ROW_VALUE_RE.finditer(tail)][:6]
    parts = [p for p in raw if p]
    out: dict[str, str | None] = {
        'mailing_street': None, 'mailing_city': None,
        'mailing_state':  None, 'mailing_zip':  None,
    }
    if not parts:
        return out
    if re.fullmatch(r'\d{5}(-\d{4})?', parts[-1]):
        out['mailing_zip'] = parts.pop()
    if parts and re.fullmatch(r'[A-Z]{2}', parts[-1]):
        out['mailing_state'] = parts.pop()
    if parts:
        out['mailing_city'] = parts.pop()
    if parts:
        out['mailing_street'] = ', '.join(parts)
    return out


_SHAPE_ARGS_JS_RE = re.compile(
    r"displayAuctionShapeData\(\s*'\d+'\s*,\s*'([A-Z]+)'\s*,\s*'([^']+)'\s*,\s*'(\d+)'\s*\)"
)

def _shape_args_from_html(html: str) -> tuple[str | None, str | None, str | None]:
    """Pull the (state, county, parcel_key) triple GovEase itself passes to the
    shape API — it's literally in the inline JS on the detail page. Authoritative."""
    m = _SHAPE_ARGS_JS_RE.search(html)
    if not m:
        return None, None, None
    return m.group(1), m.group(2), m.group(3)


def parse_detail(html: str) -> dict:
    # Some counties (e.g. Lamar, Bibb) print 'N/A' instead of leaving a field
    # blank, and report 'Total True Value: $0.00' on parcels with no assessed
    # data. Pull each field, then post-process to null-out the no-data sentinels.
    def f(label):
        v = _field(html, label)
        return None if (v is None or v.strip().upper() in ('N/A', 'NA', '')) else v

    true_value    = parse_money(f('Total True Value'))
    true_building = parse_money(f('True Building Value'))
    true_land     = parse_money(f('True Land Value'))
    # If the county reports $0.00 for True Value but N/A for both components,
    # that's "not assessed" — treat as null so the UI doesn't show a misleading $0.
    if true_value == 0 and true_building is None and true_land is None:
        true_value = None

    # Some counties give 'Assessed Value' (single field), others split into
    # 'Assessed Land' + 'Assessed Improvements'. Capture both, prefer single.
    assessed_value        = parse_money(f('Assessed Value'))
    assessed_land         = parse_money(f('Assessed Land'))
    assessed_improvements = parse_money(f('Assessed Improvements'))
    if assessed_value is None and (assessed_land is not None or assessed_improvements is not None):
        assessed_value = (assessed_land or 0) + (assessed_improvements or 0)

    out = {
        'face_value':       parse_money(f('Face Value')),
        'physical_address': f('Physical Address'),
        'legal_description': _legal_description(html),
        'assessed_value':   assessed_value,
        'assessed_land':    assessed_land,
        'assessed_improvements': assessed_improvements,
        'true_value':       true_value,
        'true_building':    true_building,
        'true_land':        true_land,
        'tax_year':         parse_int(f('Tax Year')),
        'unique_number':    f('Unique #'),
        'primary_owner':    f('Primary Owner'),
    }
    _, sh_county, sh_key = _shape_args_from_html(html)
    if sh_key:
        out['shape_key'] = sh_key
    if sh_county:
        out['shape_county'] = sh_county
    out.update(_mailing_address(html))
    return out


# ── parcel geometry (lat/lng + boundary polygon) ──────────────────────────────
SHAPE_ENDPOINT = '/Live/GetAuctionShapeData'

# GovEase's internal county slug for the shape API doesn't always match the URL
# slug. Most counties: strip the state prefix ('albarbour' -> 'barbour'). For the
# exceptions below, the API uses something else entirely (extracted from
# `displayAuctionShapeData(...)` JS calls on real detail pages).
_SHAPE_COUNTY_OVERRIDES = {
    'aljeffersonbessemer': 'jefferson',  # Bessemer is a sub-jurisdiction
    'alstclair':           'st. clair',  # period and space, not concatenated
    'alwinstin':           'winston',    # GovEase URL slug has a typo
}

def _shape_county(slug: str) -> str:
    """Resolve the slug to whatever GovEase expects in the shape API body.
    Per-county overrides take priority over the default state-prefix strip."""
    if slug in _SHAPE_COUNTY_OVERRIDES:
        return _SHAPE_COUNTY_OVERRIDES[slug]
    return slug[2:] if len(slug) > 2 and slug[:2].isalpha() else slug

def _shape_parcel_key(parcel_number: str) -> str:
    """Compute a fallback shape-API key from a parcel number.
    Strip non-digits; if >16 digits, truncate to 16 (some counties pad an extra
    leading 0 in their listings — e.g. Covington has 17-digit IDs while the
    shape API uses the 16-digit canonical form)."""
    digits = re.sub(r'\D', '', parcel_number or '')
    return digits[:16] if len(digits) > 16 else digits

def fetch_shape(s: requests.Session, state: str, slug: str,
                parcel_number: str, shape_key: str | None = None,
                shape_county: str | None = None) -> dict | None:
    """Look up lat/lng/polygon from GovEase. When the per-parcel `shape_key`
    or `shape_county` are known (from the detail-page JS), use them — they're
    authoritative. Otherwise fall back to deriving from parcel_number/slug."""
    key = shape_key or _shape_parcel_key(parcel_number)
    county = shape_county or _shape_county(slug)
    body = {
        'state':  state,
        'county': county,
        'parcelnumber': key,
    }
    try:
        r = s.post(f'{BASE}{SHAPE_ENDPOINT}', json=body,
                   headers={'X-Requested-With': 'XMLHttpRequest'}, timeout=20)
        r.raise_for_status()
        d = r.json()
        if d.get('r') != 1:
            return None
        out: dict[str, Any] = {
            'lat': float(d['l']) if d.get('l') else None,
            'lng': float(d['g']) if d.get('g') else None,
        }
        m = d.get('m')
        if isinstance(m, str) and m.strip().startswith('['):
            try:
                rings = json.loads(m)
                poly = [r for r in rings if r]
                if poly:
                    out['polygon'] = poly
            except Exception:
                pass
        elif isinstance(m, list):
            poly = [r for r in m if r]
            if poly:
                out['polygon'] = poly
        return out
    except Exception:
        return None
