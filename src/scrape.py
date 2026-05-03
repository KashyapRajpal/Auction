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

from govease import (
    fetch_counties, fetch_list_page, parse_list_grid,
    fetch_detail, parse_detail, fetch_shape,
    session, polite_sleep,
)

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


# ── counties ──────────────────────────────────────────────────────────────────
def cmd_counties() -> None:
    counties = fetch_counties(session())
    payload = {'fetched_at': _now(), 'count': len(counties), 'counties': counties}
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(COUNTIES_FILE, 'w') as f:
        json.dump(payload, f, indent=2)
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
    with open(fn, 'w') as f:
        json.dump(snap, f, indent=2)
    hits = sum(1 for p in todo if p.get('lat') is not None)
    print(f"✓ {hits}/{len(todo)} re-shaped → {fn}")
    rebuild_manifest()


def cmd_county(state: str, slug: str, county_id: int, *,
               no_details: bool = False, no_geocode: bool = False,
               limit: int | None = None, workers: int = 4) -> None:
    snap = scrape_county(state, slug, county_id,
                         fetch_details=not no_details,
                         geocode=not no_geocode, limit=limit, workers=workers)
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    out = os.path.join(SNAPSHOT_DIR, f"{state}-{slug}.json")
    with open(out, 'w') as f:
        json.dump(snap, f, indent=2)
    print(f"✓ {snap['parcel_count']} parcels → {out}")
    rebuild_manifest()


# ── manifest (frontend reads this to know which counties have data) ───────────
def rebuild_manifest() -> None:
    if not os.path.exists(SNAPSHOT_DIR):
        return
    entries = []
    for fn in sorted(os.listdir(SNAPSHOT_DIR)):
        if not fn.endswith('.json'):
            continue
        try:
            d = json.load(open(os.path.join(SNAPSHOT_DIR, fn)))
        except Exception:
            continue
        c = d.get('county', {})
        # Detect enrichment level by sampling the first parcel.
        sample = (d.get('parcels') or [None])[0]
        enriched = bool(sample and (sample.get('lat') is not None
                                    or sample.get('true_value') is not None))
        entries.append({
            'file':         fn,
            'state':        c.get('state'),
            'slug':         c.get('slug'),
            'id':           c.get('id'),
            'parcel_count': d.get('parcel_count', 0),
            'fetched_at':   d.get('fetched_at'),
            'enriched':     enriched,
        })
    with open(MANIFEST_FILE, 'w') as f:
        json.dump({'updated_at': _now(), 'count': len(entries), 'snapshots': entries},
                  f, indent=2)
    print(f"  manifest: {len(entries)} snapshots → {MANIFEST_FILE}")


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
    elif args.cmd == 'reshape':
        cmd_reshape(args.state.upper(), args.slug, args.county_id, workers=args.workers)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
