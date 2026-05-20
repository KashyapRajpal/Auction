import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
FIXTURES = Path(ROOT) / 'tests' / 'fixtures'

from bid4assets import parse_listing_page, scrape_active_county_sales


class TestBid4Assets(unittest.TestCase):
    def test_parse_listing_page_normalizes_schema(self):
        html = (FIXTURES / 'bid4assets_page1.html').read_text()
        items = parse_listing_page(html, state='CA', county_city='Los Angeles', page_url='https://example.com/page1')
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item['parcel_id'], '123-456-789')
        self.assertEqual(item['currency'], 'USD')
        self.assertEqual(item['auction_type'], 'Tax Sale')
        self.assertEqual(item['state_province'], 'CA')
        self.assertEqual(item['country'], 'US')

    def test_scrape_respects_minimum_throttle(self):
        html = (FIXTURES / 'bid4assets_page1.html').read_text()
        client = MagicMock()
        client.get.side_effect = [html, html]
        t0 = time.time()
        items = scrape_active_county_sales(
            ['https://example.com/1', 'https://example.com/2'],
            state='CA',
            county_city='Los Angeles',
            throttle_seconds=0.1,
            client=client,
        )
        elapsed = time.time() - t0
        self.assertGreaterEqual(elapsed, 1.4)
        self.assertEqual(len(items), 2)


if __name__ == '__main__':
    unittest.main()
