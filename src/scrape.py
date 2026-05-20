"""
GovEase scraper CLI — thin orchestration over src/govease.py.

Same script runs locally and in CI / service.py. Snapshots are committed; the
frontend reads them as static files (no server, no cache layer, no CORS).

Usage:
  python src/scrape.py counties
  python src/scrape.py county AL albarbour 1262
  python src/scrape.py county AL albarbour 1262 --limit 10 --no-geocode
  python src/scrape.py all
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from bid4assets import scrape_active_county_sales
from canada_municipal import parse_municipal_sales_page
from govease import (
    fetch_counties, fetch_list_page, parse_list_grid,
    fetch_detail, parse_detail, fetch_shape,
    session, polite_sleep,
)
from realauction import scrape_county_from_internal_endpoints
from unified_schema import snapshot_filename, validate_snapshot_item

# requests.Session is not thread-safe at the .request() level — give each
# worker thread its own session via thread-local storage.
_tl = threading.local()
def _thread_session():
    s = getattr(_tl, 'session', None)
    if s is None:
        s = session()
        _tl.session = s
    return s

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR      = os.path.join(ROOT, 'data')
SNAPSHOT_DIR  = os.path.join(DATA_DIR, 'snapshots')
COUNTIES_FILE = os.path.join(DATA_DIR, 'counties.json')
MANIFEST_FILE = os.path.join(DATA_DIR, 'manifest.json')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def _atomic_write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, 'w') as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


# ── counties ──────────────────────────────────────────────────────────────────
def cmd_counties() -> None:
    counties = fetch_counties(session())
    payload = {'fetched_at': _now(), 'count': len(counties), 'counties': counties}
    _atomic_write_json(COUNTIES_FILE, payload)
    print(f"✓ {len(counties)} counties → {COUNTIES_FILE}")


# ── per-county scrape ─────────────────────────────────────────────────────────
def scrape_county(state: str, slug: str, county_id: int, *,
                  fetch_details: bool = True, geocode: bool = True,
                  limit: int | None = None, workers: int = 4) -> dict:
    s = session()
    print(f"[{state}/{slug}/{county_id}] paginating list…")
    parcels: list[dict] = []
    page = 1
    while True:
        grid = fetch_list_page(s, state, slug, county_id, page)
        rows = parse_list_grid(grid)
        if not rows:
            break
        parcels.extend(rows)
        print(f"  page {page}: +{len(rows)} (running total {len(parcels)})")
        if limit and len(parcels) >= limit:
            parcels = parcels[:limit]
            break
        page += 1
        polite_sleep(1.0, 2.5)

    if fetch_details:
        _parallel_details(parcels, state, slug, county_id, workers)
    if geocode:
        _parallel_shapes(parcels, state, slug, workers)

    return {
        'county':       {'state': state, 'slug': slug, 'id': county_id},
        'fetched_at':   _now(),
        'parcel_count': len(parcels),
        'parcels':      parcels,
    }


def _parallel_details(parcels: list[dict], state: str, slug: str,
                      county_id: int, workers: int) -> None:
    """Fetch + parse detail pages concurrently. Each worker has its own
    requests.Session and a small jittered sleep to keep the aggregate rate polite."""
    print(f"  fetching detail pages for {len(parcels)} parcels (workers={workers})…")

    def one(p: dict) -> tuple[dict, Exception | None]:
        s = _thread_session()
        try:
            html = fetch_detail(s, state, slug, county_id,
                                p['lot_id'], p['parcel_slug'])
            p.update(parse_detail(html))
        except Exception as e:
            return p, e
        polite_sleep(0.5, 1.5)
        return p, None

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, p) for p in parcels]
        for fut in as_completed(futs):
            p, err = fut.result()
            done += 1
            if err:
                print(f"    [{done}/{len(parcels)}] {p['lot_id']} ERR: {err}")
            if done % 50 == 0 or done == len(parcels):
                print(f"    {done}/{len(parcels)} detail pages done")


def _parallel_shapes(parcels: list[dict], state: str, slug: str,
                     workers: int) -> None:
    print(f"  fetching shape data for {len(parcels)} parcels (workers={workers})…")
    hits_lock = threading.Lock()
    counters = {'done': 0, 'hits': 0}

    def one(p: dict) -> None:
        s = _thread_session()
        shape = fetch_shape(s, state, slug,
                            p.get('parcel_number', ''),
                            shape_key=p.get('shape_key'),
                            shape_county=p.get('shape_county'))
        if shape and shape.get('lat') is not None:
            p.update(shape)
            with hits_lock: counters['hits'] += 1
        else:
            p.setdefault('lat', None); p.setdefault('lng', None)
        polite_sleep(0.3, 1.0)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, p) for p in parcels]
        for fut in as_completed(futs):
            fut.result()
            with hits_lock:
                counters['done'] += 1
                d, h = counters['done'], counters['hits']
            if d % 50 == 0 or d == len(parcels):
                print(f"    {d}/{len(parcels)} shapes fetched ({h} hits)")


def cmd_reshape(state: str, slug: str, county_id: int, *, workers: int = 4) -> None:
    """Re-run only the shape API on parcels missing lat/lng. Useful when the
    parcel-key derivation rule changes (e.g. Covington's 17-digit format) and
    you don't want to re-fetch every detail page."""
    fn = os.path.join(SNAPSHOT_DIR, f"{state}-{slug}.json")
    if not os.path.exists(fn):
        print(f"no snapshot at {fn}; run `county` first"); return
    snap = json.load(open(fn))
    parcels = snap.get('parcels', [])
    todo = [p for p in parcels if p.get('lat') is None]
    if not todo:
        print(f"  nothing to do — all {len(parcels)} parcels already have lat/lng")
        return
    print(f"[{state}/{slug}/{county_id}] re-shaping {len(todo)}/{len(parcels)} parcels…")
    _parallel_shapes(todo, state, slug, workers)
    snap['fetched_at'] = _now()
    _atomic_write_json(fn, snap)
    hits = sum(1 for p in todo if p.get('lat') is not None)
    print(f"✓ {hits}/{len(todo)} re-shaped → {fn}")
    rebuild_manifest()


def cmd_county(state: str, slug: str, county_id: int, *,
               no_details: bool = False, no_geocode: bool = False,
               limit: int | None = None, workers: int = 4) -> None:
    snap = scrape_county(state, slug, county_id,
                         fetch_details=not no_details,
                         geocode=not no_geocode, limit=limit, workers=workers)
    out = os.path.join(SNAPSHOT_DIR, f"{state}-{slug}.json")
    _atomic_write_json(out, snap)
    print(f"✓ {snap['parcel_count']} parcels → {out}")
    rebuild_manifest()


# ── manifest (frontend reads this to know which counties have data) ───────────
def rebuild_manifest() -> None:
    if not os.path.exists(SNAPSHOT_DIR):
        return
    entries = []
    for root, _, files in os.walk(SNAPSHOT_DIR):
      for fn in sorted(files):
        if not fn.endswith('.json'):
            continue
        fp = os.path.join(root, fn)
        rel = os.path.relpath(fp, SNAPSHOT_DIR).replace(os.sep, '/')
        try:
            d = json.load(open(fp))
        except Exception:
            continue
        if not isinstance(d, dict) or not isinstance(d.get('county'), dict):
            continue
        c = d.get('county', {})
        # Detect enrichment level by sampling the first parcel.
        sample = (d.get('parcels') or [None])[0]
        enriched = bool(sample and (sample.get('lat') is not None
                                    or sample.get('true_value') is not None))
        entries.append({
            'file':         rel,
            'state':        c.get('state'),
            'slug':         c.get('slug'),
            'id':           c.get('id'),
            'parcel_count': d.get('parcel_count', 0),
            'fetched_at':   d.get('fetched_at'),
            'enriched':     enriched,
        })
    _atomic_write_json(MANIFEST_FILE, {'updated_at': _now(), 'count': len(entries), 'snapshots': entries})
    print(f"  manifest: {len(entries)} snapshots → {MANIFEST_FILE}")


def _write_unified_snapshot(country: str, state: str, county: str, items: list[dict]) -> None:
    for item in items:
        validate_snapshot_item(item)
    out = os.path.join(SNAPSHOT_DIR, snapshot_filename(country, state, county))
    payload = {
        'fetched_at': _now(),
        'source': f'{country}-{state}-{county}',
        'count': len(items),
        'items': items,
    }
    _atomic_write_json(out, payload)
    print(f"✓ {len(items)} unified rows → {out}")


def cmd_multi() -> None:
    changed = False

    bid4assets_sources = [
        {
            'state': 'CA',
            'county_city': 'Los Angeles',
            'urls': ['https://www.bid4assets.com/sales/index.cfm?partnerstateid=5'],
        },
        {
            'state': 'WA',
            'county_city': 'King',
            'urls': ['https://www.bid4assets.com/sales/index.cfm?partnerstateid=49'],
        },
    ]
    for src in bid4assets_sources:
        try:
            items = scrape_active_county_sales(src['urls'], state=src['state'], county_city=src['county_city'])
            if items:
                _write_unified_snapshot('US', src['state'], src['county_city'], items)
                changed = True
        except Exception as e:
            print(f"[warn] Bid4Assets {src['state']}/{src['county_city']} failed: {e}")

    realauction_sources = [
        {
            'state': 'FL',
            'county_city': 'Miami-Dade',
            'endpoints': ['https://www.realauction.com/api/listings?county=miami-dade'],
        },
        {
            'state': 'AZ',
            'county_city': 'Maricopa',
            'endpoints': ['https://www.realauction.com/api/listings?county=maricopa'],
        },
    ]
    for src in realauction_sources:
        try:
            items = scrape_county_from_internal_endpoints(src['endpoints'], state=src['state'], county_city=src['county_city'])
            if items:
                _write_unified_snapshot('US', src['state'], src['county_city'], items)
                changed = True
        except Exception as e:
            print(f"[warn] Realauction {src['state']}/{src['county_city']} failed: {e}")

    canada_sources = [
        {
            'platform': 'Calgary_Gov',
            'province': 'AB',
            'county_city': 'Calgary',
            'url': 'https://www.calgary.ca/taxes/property-tax/tax-sale.html',
        },
        {
            'platform': 'Toronto_Gov',
            'province': 'ON',
            'county_city': 'Toronto',
            'url': 'https://www.toronto.ca/services-payments/property-taxes-utilities/property-tax/tax-sales/',
        },
    ]
    s = session()
    for src in canada_sources:
        try:
            r = s.get(src['url'], timeout=30)
            r.raise_for_status()
            items = parse_municipal_sales_page(
                r.text,
                source_platform=src['platform'],
                province=src['province'],
                county_city=src['county_city'],
                source_url=src['url'],
            )
            if items:
                _write_unified_snapshot('CA', src['province'], src['county_city'], items)
                changed = True
        except Exception as e:
            print(f"[warn] Canada municipal {src['province']}/{src['county_city']} failed: {e}")

    if changed:
        rebuild_manifest()


# ── all-counties (CI & service.py both call this) ─────────────────────────────
def cmd_all(*, no_details: bool = False, no_geocode: bool = False,
            limit: int | None = None, workers: int = 4) -> None:
    if not os.path.exists(COUNTIES_FILE):
        cmd_counties()
    counties = json.load(open(COUNTIES_FILE))['counties']
    random.shuffle(counties)
    for c in counties:
        cmd_county(c['state'], c['slug'], c['id'],
                   no_details=no_details, no_geocode=no_geocode,
                   limit=limit, workers=workers)
        polite_sleep(10.0, 30.0)


# ── CLI ───────────────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('counties', help='refresh data/counties.json')

    pc = sub.add_parser('county', help='scrape one county')
    pc.add_argument('state'); pc.add_argument('slug'); pc.add_argument('county_id', type=int)
    pc.add_argument('--no-details', action='store_true', help='skip detail fetches')
    pc.add_argument('--no-geocode', action='store_true', help='skip lat/lng + polygon fetches')
    pc.add_argument('--limit', type=int, default=None, help='cap parcels (smoke test)')
    pc.add_argument('--workers', type=int, default=4,
                    help='concurrent detail/shape workers (default 4)')

    pa = sub.add_parser('all', help='scrape every county in counties.json')
    pa.add_argument('--no-details', action='store_true')
    pa.add_argument('--no-geocode', action='store_true')
    pa.add_argument('--limit', type=int, default=None)
    pa.add_argument('--workers', type=int, default=4)

    sub.add_parser('multi', help='scrape Bid4Assets, Realauction, and Canadian municipal sources')

    pr = sub.add_parser('reshape', help='re-run shape API on parcels missing lat/lng')
    pr.add_argument('state'); pr.add_argument('slug'); pr.add_argument('county_id', type=int)
    pr.add_argument('--workers', type=int, default=4)

    args = p.parse_args(argv)
    if args.cmd == 'counties':
        cmd_counties()
    elif args.cmd == 'county':
        cmd_county(args.state.upper(), args.slug, args.county_id,
                   no_details=args.no_details, no_geocode=args.no_geocode,
                   limit=args.limit, workers=args.workers)
    elif args.cmd == 'all':
        cmd_all(no_details=args.no_details, no_geocode=args.no_geocode,
                limit=args.limit, workers=args.workers)
    elif args.cmd == 'multi':
        cmd_multi()
    elif args.cmd == 'reshape':
        cmd_reshape(args.state.upper(), args.slug, args.county_id, workers=args.workers)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
