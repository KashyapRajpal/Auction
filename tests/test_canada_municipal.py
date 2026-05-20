import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from canada_municipal import parse_municipal_sales_page


class TestCanadaMunicipal(unittest.TestCase):
    def test_parse_municipal_sales_page(self):
        html = open(os.path.join(ROOT, 'tests/fixtures/calgary_tax_sales.html')).read()
        items = parse_municipal_sales_page(
            html,
            source_platform='Calgary_Gov',
            province='AB',
            county_city='Calgary',
            source_url='https://calgary.example/sales',
        )
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['country'], 'CA')
        self.assertEqual(items[0]['currency'], 'CAD')
        self.assertEqual(items[1]['auction_type'], 'Government Disposal')


if __name__ == '__main__':
    unittest.main()
