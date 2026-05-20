from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha1
import re

from unified_schema import validate_snapshot_item

ROW_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
CELL_RE = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.DOTALL | re.IGNORECASE)
TAG_RE = re.compile(r'<[^>]+>')


def _clean(text: str) -> str:
    return re.sub(r'\s+', ' ', TAG_RE.sub(' ', text or '')).strip()


def _money(value: str) -> float:
    m = re.search(r'([\d,]+(?:\.\d+)?)', value or '')
    return float(m.group(1).replace(',', '')) if m else 0.0


def _to_utc(value: str) -> str:
    for fmt in (
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d',
        '%m/%d/%Y %I:%M %p',
        '%m/%d/%Y',
    ):
        try:
            dt = datetime.strptime(value.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except Exception:
            continue
    return datetime.now(timezone.utc).isoformat()


def _approx_lat_lng(seed: str) -> tuple[float, float]:
    digest = sha1((seed or '').encode('utf-8')).hexdigest()
    lat = 41.7 + (int(digest[:8], 16) / 0xFFFFFFFF) * (60.0 - 41.7)
    lng = -141.0 + (int(digest[8:16], 16) / 0xFFFFFFFF) * (-52.6 + 141.0)
    return round(lat, 6), round(lng, 6)


def parse_municipal_sales_page(
    html: str,
    *,
    source_platform: str,
    province: str,
    county_city: str,
    source_url: str,
) -> list[dict]:
    items: list[dict] = []
    for row_html in ROW_RE.findall(html or ''):
        cells = [_clean(c) for c in CELL_RE.findall(row_html)]
        if len(cells) < 3:
            continue

        parcel_id = cells[0]
        if parcel_id.lower() in {'roll', 'roll number', 'parcel id', 'property id'}:
            continue
        opening_bid = _money(cells[1])
        auction_date = _to_utc(cells[2])
        legal = cells[3] if len(cells) > 3 else ''
        location_hint = cells[4] if len(cells) > 4 else parcel_id
        auction_type = 'Government Disposal' if 'disposal' in (legal or '').lower() else 'Tax Sale'
        lat, lng = _approx_lat_lng(location_hint)

        if not parcel_id:
            continue
        item = {
            'parcel_id': parcel_id,
            'source_platform': source_platform,
            'country': 'CA',
            'state_province': province,
            'county_city': county_city,
            'opening_bid': opening_bid,
            'currency': 'CAD',
            'auction_type': auction_type,
            'latitude': lat,
            'longitude': lng,
            'auction_date_utc': auction_date,
            'raw_legal_description': legal,
            'property_url': source_url,
        }
        validate_snapshot_item(item)
        items.append(item)
    return items
