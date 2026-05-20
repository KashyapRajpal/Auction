from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha1
import requests

from unified_schema import validate_snapshot_item


def _float(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(',', '').replace('$', '').strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def _to_utc(value: object) -> str:
    text = str(value or '').strip()
    if not text:
        return datetime.now(timezone.utc).isoformat()
    for suffix in ('Z', '+00:00'):
        if text.endswith(suffix):
            try:
                return datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()
            except ValueError:
                pass
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%m/%d/%Y %I:%M %p'):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return datetime.now(timezone.utc).isoformat()


def _auction_type(kind: object) -> str:
    text = str(kind or '').lower()
    if 'lien' in text or 'certificate' in text:
        return 'Tax Lien'
    if 'deed' in text:
        return 'Tax Deed'
    if 'disposal' in text or 'government' in text:
        return 'Government Disposal'
    return 'Tax Sale'


def _approx_lat_lng(value: str) -> tuple[float, float]:
    digest = sha1((value or '').encode('utf-8')).hexdigest()
    lat = 24.5 + (int(digest[:8], 16) / 0xFFFFFFFF) * (49.5 - 24.5)
    lng = -124.8 + (int(digest[8:16], 16) / 0xFFFFFFFF) * (-66.9 + 124.8)
    return round(lat, 6), round(lng, 6)


def parse_internal_json(payload: dict, *, state: str, county_city: str, endpoint_url: str) -> list[dict]:
    listings = payload.get('listings') if isinstance(payload, dict) else None
    if listings is None and isinstance(payload, dict):
        listings = payload.get('items')
    if listings is None and isinstance(payload, list):
        listings = payload
    listings = listings or []

    out: list[dict] = []
    for row in listings:
        parcel_id = str(row.get('parcel_id') or row.get('apn') or row.get('roll_number') or '').strip()
        if not parcel_id:
            continue

        lat, lng = _approx_lat_lng(str(row.get('address') or parcel_id))
        item = {
            'parcel_id': parcel_id,
            'source_platform': 'Realauction',
            'country': 'US',
            'state_province': state,
            'county_city': county_city,
            'opening_bid': _float(row.get('opening_bid') or row.get('minimum_bid') or row.get('certificate_amount')),
            'currency': str(row.get('currency') or 'USD').upper(),
            'auction_type': _auction_type(row.get('auction_type') or row.get('sale_type')),
            'latitude': row.get('latitude', lat),
            'longitude': row.get('longitude', lng),
            'auction_date_utc': _to_utc(row.get('auction_end_utc') or row.get('end_time') or row.get('sale_date')),
            'raw_legal_description': str(row.get('legal_description') or ''),
            'property_url': str(row.get('property_url') or row.get('url') or endpoint_url),
        }
        validate_snapshot_item(item)
        out.append(item)
    return out


def scrape_county_from_internal_endpoints(
    endpoint_urls: list[str], *,
    state: str,
    county_city: str,
    session: requests.Session | None = None,
) -> list[dict]:
    s = session or requests.Session()
    all_items: list[dict] = []
    for endpoint in endpoint_urls:
        response = s.get(endpoint, timeout=30)
        response.raise_for_status()
        payload = response.json()
        all_items.extend(parse_internal_json(payload, state=state, county_city=county_city, endpoint_url=endpoint))
    return all_items
