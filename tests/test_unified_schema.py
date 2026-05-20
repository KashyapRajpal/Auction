import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from unified_schema import snapshot_filename, validate_snapshot_item


class TestUnifiedSchema(unittest.TestCase):
    def test_snapshot_filename_structure(self):
        self.assertEqual(snapshot_filename('US', 'CA', 'Los Angeles'), 'US_CA_LOS_ANGELES.json')

    def test_validate_snapshot_item(self):
        item = {
            'parcel_id': '123-ABC',
            'source_platform': 'Bid4Assets',
            'country': 'US',
            'state_province': 'CA',
            'county_city': 'Los Angeles',
            'opening_bid': 1250.0,
            'currency': 'USD',
            'auction_type': 'Tax Sale',
            'latitude': 34.05,
            'longitude': -118.24,
            'auction_date_utc': '2026-06-01T15:00:00+00:00',
            'raw_legal_description': 'Lot 12 Tract 91',
            'property_url': 'https://example.com/property/1',
        }
        validate_snapshot_item(item)


if __name__ == '__main__':
    unittest.main()
