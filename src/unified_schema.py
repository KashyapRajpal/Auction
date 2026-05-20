from __future__ import annotations

from datetime import datetime
import re

REQUIRED_FIELDS = [
    'parcel_id', 'source_platform', 'country', 'state_province', 'county_city',
    'opening_bid', 'currency', 'auction_type', 'latitude', 'longitude', 'auction_date_utc'
]

AUCTION_TYPES = {'Tax Lien', 'Tax Deed', 'Tax Sale', 'Government Disposal'}

UNIFIED_AUCTION_SCHEMA: dict = {
    '$schema': 'http://json-schema.org/draft-07/schema#',
    'title': 'UnifiedAuctionSnapshot',
    'type': 'object',
    'required': REQUIRED_FIELDS,
    'properties': {
        'parcel_id': {'type': 'string'},
        'source_platform': {'type': 'string'},
        'country': {'type': 'string'},
        'state_province': {'type': 'string'},
        'county_city': {'type': 'string'},
        'opening_bid': {'type': 'number'},
        'currency': {'type': 'string'},
        'auction_type': {'type': 'string', 'enum': sorted(AUCTION_TYPES)},
        'latitude': {'type': ['number', 'null']},
        'longitude': {'type': ['number', 'null']},
        'auction_date_utc': {'type': 'string', 'format': 'date-time'},
        'raw_legal_description': {'type': 'string'},
        'property_url': {'type': 'string', 'format': 'uri'},
    },
}


def sanitize_token(value: str) -> str:
    value = (value or '').strip().upper()
    value = re.sub(r'[^A-Z0-9]+', '_', value)
    value = re.sub(r'_+', '_', value).strip('_')
    return value or 'UNKNOWN'


def snapshot_filename(country: str, state_province: str, county_city: str) -> str:
    return f"{sanitize_token(country)}_{sanitize_token(state_province)}_{sanitize_token(county_city)}.json"


def _is_number_or_none(value: object) -> bool:
    return value is None or isinstance(value, (int, float))


def _is_uri(value: str) -> bool:
    return isinstance(value, str) and (value.startswith('https://') or value.startswith('http://'))


def _is_datetime(value: str) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.replace('Z', '+00:00')
    try:
        datetime.fromisoformat(candidate)
        return True
    except ValueError:
        return False


def validate_snapshot_item(item: dict) -> None:
    for field in REQUIRED_FIELDS:
        if field not in item:
            raise ValueError(f'missing required field: {field}')

    if not isinstance(item['parcel_id'], str) or not item['parcel_id'].strip():
        raise ValueError('parcel_id must be a non-empty string')
    if not isinstance(item['source_platform'], str) or not item['source_platform'].strip():
        raise ValueError('source_platform must be a non-empty string')
    if not isinstance(item['country'], str) or len(item['country']) != 2:
        raise ValueError('country must be ISO alpha-2 code')
    if not isinstance(item['currency'], str) or len(item['currency']) != 3:
        raise ValueError('currency must be ISO alpha-3 code')
    if not isinstance(item['opening_bid'], (int, float)):
        raise ValueError('opening_bid must be numeric')
    if item['auction_type'] not in AUCTION_TYPES:
        raise ValueError('auction_type is not in allowed enum')
    if not _is_number_or_none(item['latitude']) or not _is_number_or_none(item['longitude']):
        raise ValueError('latitude/longitude must be number or null')
    if not _is_datetime(item['auction_date_utc']):
        raise ValueError('auction_date_utc must be ISO date-time')
    if 'property_url' in item and item['property_url'] is not None and not _is_uri(item['property_url']):
        raise ValueError('property_url must be a valid URI')
