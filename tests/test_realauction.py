import json
import os
import sys
import unittest
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from realauction import parse_internal_json, scrape_county_from_internal_endpoints


class TestRealauction(unittest.TestCase):
    def test_parse_internal_json_normalizes(self):
        payload = json.load(open(os.path.join(ROOT, 'tests/fixtures/realauction_listings.json')))
        items = parse_internal_json(payload, state='FL', county_city='Miami-Dade', endpoint_url='https://api.example.com')
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['auction_type'], 'Tax Deed')
        self.assertEqual(items[1]['auction_type'], 'Tax Lien')
        self.assertEqual(items[0]['currency'], 'USD')

    def test_scrape_uses_session_without_live_network(self):
        payload = json.load(open(os.path.join(ROOT, 'tests/fixtures/realauction_listings.json')))
        response = MagicMock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        session = MagicMock()
        session.get.return_value = response

        items = scrape_county_from_internal_endpoints(
            ['https://api.example.com/listings'],
            state='FL',
            county_city='Miami-Dade',
            session=session,
        )
        self.assertEqual(len(items), 2)
        self.assertEqual(session.get.call_count, 1)


if __name__ == '__main__':
    unittest.main()
