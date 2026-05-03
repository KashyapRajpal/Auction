"""
Retroactively backfill lat/lng on existing snapshots.

Walks every data/snapshots/*.json, identifies counties whose parcels are
under-geocoded (typically because GovEase's shape-API county slug differs
from the URL slug — see Jefferson(Bessemer) → 'jefferson', St. Clair →
'st. clair', Winston → 'winston'), then:

  1. Fetches one parcel's detail page.
  2. Reads the canonical (county_slug, parcel_key) from the inline
     `displayAuctionShapeData(...)` JS call.
  3. Reshapes every missing-lat parcel in parallel using that slug.
  4. Updates the snapshot in place + rebuilds data/manifest.json.

Usage:
  python tools/fix-latlng.py                  # all under-geocoded counties
  python tools/fix-latlng.py --threshold 0.8  # consider <80%% coverage broken
  python tools/fix-latlng.py --workers 6      # default 4
  python tools/fix-latlng.py --county AL aljeffersonbessemer 1312   # one county
  python tools/fix-latlng.py --dry-run        # only report what would happen
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from govease import (
    fetch_detail, parse_detail, fetch_shape, session, polite_sleep,
)
from scrape import SNAPSHOT_DIR, MANIFEST_FILE, _now, rebuild_manifest


def _coverage(snap: dict) -> tuple[int, int]:
    parcels = snap.get('parcels') or []
    if not parcels:
        return 0, 0
    with_lat = sum(1 for p in parcels if p.get('lat') is not None)
    return with_lat, len(parcels)


def _is_enriched(snap: dict) -> bool:
    """Detail data was fetched (so we have parcel_slugs we can use)."""
    parcels = snap.get('parcels') or []
    return any(p.get('true_value') is not None or p.get('physical_address')
               or p.get('shape_key') or p.get('mailing_state')
               for p in parcels)


def _discover_canonical_args(s, state: str, slug: str, county_id: int,
                              parcels: list[dict]) -> tuple[str | None, dict[int, str]]:
    """Fetch one parcel's detail page and pull the (county, key) GovEase
    actually uses for the shape API. Also returns a map of lot_id → shape_key
    when easily extractable for that one sample (we don't pre-fetch the rest;
    fetch_shape will derive keys for the others)."""
    sample = next((p for p in parcels if p.get('parcel_slug')), None)
    if not sample:
        return None, {}
    try:
        html = fetch_detail(s, state, slug, county_id,
                            sample['lot_id'], sample['parcel_slug'])
    except Exception as e:
        print(f"    discover fetch failed: {e}")
        return None, {}
    parsed = parse_detail(html)
    return parsed.get('shape_county'), {sample['lot_id']: parsed.get('shape_key')} if parsed.get('shape_key') else {}


def fix_one(state: str, slug: str, county_id: int, *,
            workers: int, dry_run: bool) -> dict:
    fn = os.path.join(SNAPSHOT_DIR, f"{state}-{slug}.json")
    if not os.path.exists(fn):
        return {'state': state, 'slug': slug, 'status': 'no-snapshot'}
    snap = json.load(open(fn))
    parcels = snap.get('parcels') or []
    todo = [p for p in parcels if p.get('lat') is None]
    have_lat, total = _coverage(snap)
    if not todo:
        return {'state': state, 'slug': slug, 'status': 'already-complete',
                'coverage': f'{have_lat}/{total}'}
    if not _is_enriched(snap):
        return {'state': state, 'slug': slug, 'status': 'list-only-skip',
                'note': 'no detail data — run `scrape.py county` first'}

    s = session()

    # 1. Probe: try first 3 parcels with whatever info we already have.
    probe = [fetch_shape(s, state, slug,
                         p.get('parcel_number', ''),
                         shape_key=p.get('shape_key'),
                         shape_county=p.get('shape_county'))
             for p in todo[:3]]
    polite_sleep(0.5, 1.0)
    probe_hits = sum(1 for r in probe if r and r.get('lat'))

    discovered_county = None
    if probe_hits == 0:
        # 2. Discover canonical slug from a real detail page.
        print(f"  [{state}-{slug}] probe failed — discovering canonical slug…")
        discovered_county, hint_keys = _discover_canonical_args(
            s, state, slug, county_id, parcels)
        if discovered_county:
            print(f"  [{state}-{slug}] canonical shape_county = {discovered_county!r}")
            for p in todo:
                p.setdefault('shape_county', discovered_county)
            for lot_id, key in hint_keys.items():
                if key:
                    for p in todo:
                        if p['lot_id'] == lot_id:
                            p['shape_key'] = key
        else:
            return {'state': state, 'slug': slug, 'status': 'discovery-failed',
                    'note': 'detail page had no displayAuctionShapeData call'}

    if dry_run:
        return {'state': state, 'slug': slug, 'status': 'would-reshape',
                'todo': len(todo), 'discovered': discovered_county}

    # 3. Reshape in parallel.
    print(f"  [{state}-{slug}] reshaping {len(todo)} parcels with {workers} workers…")
    hits = 0

    def one(p):
        nonlocal hits
        r = fetch_shape(s, state, slug,
                        p.get('parcel_number', ''),
                        shape_key=p.get('shape_key'),
                        shape_county=p.get('shape_county'))
        if r and r.get('lat') is not None:
            p.update(r)
            return True
        return False

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, p) for p in todo]
        for fut in as_completed(futs):
            if fut.result():
                hits += 1
            done += 1
            if done % 100 == 0 or done == len(todo):
                print(f"    {done}/{len(todo)} ({hits} hits)")

    snap['fetched_at'] = _now()
    with open(fn, 'w') as f:
        json.dump(snap, f, indent=2)
    return {'state': state, 'slug': slug, 'status': 'fixed',
            'reshaped': len(todo), 'new_hits': hits,
            'discovered_county': discovered_county}


def iter_under_geocoded(threshold: float) -> Iterator[tuple[str, str, int]]:
    for fn in sorted(glob.glob(os.path.join(SNAPSHOT_DIR, '*.json'))):
        snap = json.load(open(fn))
        parcels = snap.get('parcels') or []
        if not parcels:
            continue
        if not _is_enriched(snap):
            continue
        with_lat, total = _coverage(snap)
        if total == 0 or with_lat / total >= threshold:
            continue
        c = snap.get('county') or {}
        if not c.get('state'):
            continue
        yield c['state'], c['slug'], c['id']


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--threshold', type=float, default=0.5,
                    help='re-shape any county whose lat coverage is below this fraction (default 0.5)')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--county', nargs=3, metavar=('STATE', 'SLUG', 'ID'),
                    help='fix exactly one county; bypasses the threshold scan')
    args = ap.parse_args(argv)

    if args.county:
        state, slug, county_id = args.county[0].upper(), args.county[1], int(args.county[2])
        targets = [(state, slug, county_id)]
    else:
        targets = list(iter_under_geocoded(args.threshold))

    if not targets:
        print('No under-geocoded counties found. Nothing to do.')
        return 0

    print(f"Will process {len(targets)} county(ies): "
          f"{', '.join(f'{s}-{sl}' for s,sl,_ in targets)}")
    if args.dry_run:
        print("(dry-run — no requests will be sent beyond per-county probes)")

    results = []
    for state, slug, cid in targets:
        results.append(fix_one(state, slug, cid,
                                workers=args.workers, dry_run=args.dry_run))

    print('\n=== Summary ===')
    for r in results:
        print(f"  {r['state']}-{r['slug']:25s}  {r['status']:18s}  "
              f"{r.get('reshaped','-'):>5}/{r.get('new_hits','-'):>5}  "
              f"{r.get('discovered_county') or ''}")

    if not args.dry_run and any(r['status'] == 'fixed' for r in results):
        rebuild_manifest()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
