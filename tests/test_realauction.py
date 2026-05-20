import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
FIXTURES = Path(ROOT) / 'tests' / 'fixtures'

from realauction import parse_internal_json, scrape_county_from_internal_endpoints


class TestRealauction(unittest.TestCase):
    def test_parse_internal_json_normalizes(self):
        payload = json.loads((FIXTURES / 'realauction_listings.json').read_text())
        items = parse_internal_json(payload, state='FL', county_city='Miami-Dade', endpoint_url='https://api.example.com')
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['auction_type'], 'Tax Deed')
        self.assertEqual(items[1]['auction_type'], 'Tax Lien')
        self.assertEqual(items[0]['currency'], 'USD')

    def test_scrape_uses_session_without_live_network(self):
        payload = json.loads((FIXTURES / 'realauction_listings.json').read_text())
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
