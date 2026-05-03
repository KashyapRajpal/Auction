"""
Long-running scraper service. Resumable, signal-safe, polite.

Two phases:
  1. List-only sweep — ~1h 45m for all 113 counties (30–75s polite sleep
     between counties + the list fetch itself). Populates the dropdown so
     every county shows a parcel count. Most counties return 0 parcels
     outside their active sale window, so this sweep is mostly empty hits.
  2. Enrichment — full detail + shape per county. Multi-hour to multi-day
     depending on how many counties have parcels and how big they are; runs
     until done or killed.

State lives in data/service-state.json. Killing the process (Ctrl-C, SIGTERM,
session end) is safe — state is saved after each county.

Usage:
  python src/service.py                     # phase 1 then phase 2, default pacing
  python src/service.py --interval 60       # 60 s gap between phase-2 counties
  python src/service.py --limit 100         # cap parcels per county (faster coverage)
  python src/service.py --skip-phase1       # already have basic data
  python src/service.py --status            # print state + exit, no scraping
"""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sys
import time
from datetime import datetime, timezone

# scrape.py and govease.py live alongside this file
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scrape import (
    scrape_county, rebuild_manifest, cmd_counties,
    SNAPSHOT_DIR, COUNTIES_FILE, _now,
)
from govease import polite_sleep

STATE_FILE = os.path.join(os.path.dirname(SNAPSHOT_DIR), 'service-state.json')


# ── state ─────────────────────────────────────────────────────────────────────
def _fresh_state() -> dict:
    return {
        'started_at': _now(),
        'phase':      'list',          # 'list' → 'enrich' → 'done'
        'phase1_done': [],             # county IDs that have a basic snapshot
        'phase2_done': [],             # county IDs that have full enrichment
        'last_county': None,
        'errors':     [],
    }

def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return _fresh_state()
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return _fresh_state()

def save_state(s: dict) -> None:
    s['updated_at'] = _now()
    with open(STATE_FILE, 'w') as f:
        json.dump(s, f, indent=2)


# ── logging ───────────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ── phases ────────────────────────────────────────────────────────────────────
def _write_snapshot(snap: dict) -> None:
    c = snap['county']
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    out = os.path.join(SNAPSHOT_DIR, f"{c['state']}-{c['slug']}.json")
    with open(out, 'w') as f:
        json.dump(snap, f, indent=2)


def _county_parcel_count(c: dict) -> int:
    """Parcel count from existing snapshot; -1 if unknown."""
    path = os.path.join(SNAPSHOT_DIR, f"{c['state']}-{c['slug']}.json")
    if not os.path.exists(path):
        return -1
    try:
        return int(json.load(open(path)).get('parcel_count', -1))
    except Exception:
        return -1


def phase1_list_only(counties: list[dict], state: dict, limit: int | None) -> None:
    todo = [c for c in counties if c['id'] not in state['phase1_done']]
    if not todo:
        log("Phase 1 already complete.")
        return
    log(f"Phase 1: list-only sweep — {len(todo)} counties to do "
        f"({len(state['phase1_done'])}/{len(counties)} already done)")
    random.shuffle(todo)
    for i, c in enumerate(todo, 1):
        state['last_county'] = c['label']
        try:
            log(f"  [{i}/{len(todo)}] {c['label']} — list-only")
            snap = scrape_county(c['state'], c['slug'], c['id'],
                                 fetch_details=False, geocode=False, limit=limit)
            _write_snapshot(snap)
            rebuild_manifest()
            state['phase1_done'].append(c['id'])
            save_state(state)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log(f"    ERR: {e}")
            state['errors'].append({'when': _now(), 'county': c['label'], 'phase': 1, 'err': str(e)})
            save_state(state)
        
        polite_sleep(30.0, 75.0) # 30–75s random sleep between counties
    state['phase'] = 'enrich'
    save_state(state)
    log("Phase 1 complete — moving to enrichment.")


def phase2_enrich(counties: list[dict], state: dict,
                  interval: int, limit: int | None,
                  workers: int = 4) -> None:
    todo = [c for c in counties if c['id'] not in state['phase2_done']]
    if not todo:
        log("Phase 2 already complete — all counties enriched.")
        state['phase'] = 'done'; save_state(state)
        return
    log(f"Phase 2: enrichment — {len(todo)} counties to do "
        f"({len(state['phase2_done'])}/{len(counties)} already done); "
        f"interval ~{interval}s between counties")
    # Sort by parcel count descending; unknown counts (-1) go first so we
    # still process counties without a phase 1 snapshot. Zero-parcel counties
    # land at the end — once we hit the first one, all remaining are 0 too.
    def _sort_key(c):
        pc = _county_parcel_count(c)
        if pc < 0:
            return (0, 0)        # unknown — process first
        return (1, -pc)          # known — larger first; 0s land at the end
    todo.sort(key=_sort_key)
    for i, c in enumerate(todo, 1):
        if _county_parcel_count(c) == 0:
            empties = todo[i-1:]
            log(f"  hit 0-parcel county — marking remaining {len(empties)} as done.")
            for e in empties:
                if e['id'] not in state['phase2_done']:
                    state['phase2_done'].append(e['id'])
            save_state(state)
            break
        # Random sleep 0.5×–1.25× interval before each county
        wait = random.uniform(0.5, 1.25) * interval 
        log(f"  sleeping {wait:.0f}s before next county…")
        time.sleep(wait)
        state['last_county'] = c['label']
        try:
            log(f"  [{i}/{len(todo)}] {c['label']} — full enrichment")
            t0 = time.time()
            snap = scrape_county(c['state'], c['slug'], c['id'],
                                 fetch_details=True, geocode=True,
                                 limit=limit, workers=workers)
            _write_snapshot(snap)
            rebuild_manifest()
            state['phase2_done'].append(c['id'])
            save_state(state)
            elapsed = time.time() - t0
            log(f"    ✓ {snap['parcel_count']} parcels in {elapsed/60:.1f}min "
                f"(progress {len(state['phase2_done'])}/{len(counties)})")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log(f"    ERR: {e}")
            state['errors'].append({'when': _now(), 'county': c['label'], 'phase': 2, 'err': str(e)})
            save_state(state)
    state['phase'] = 'done'
    save_state(state)
    log("Phase 2 complete — all counties enriched!")


# ── entrypoints ───────────────────────────────────────────────────────────────
def cmd_status() -> int:
    if not os.path.exists(STATE_FILE):
        print("No service state yet. Run `python src/service.py` to start.")
        return 0
    s = json.load(open(STATE_FILE))
    counties_total = (json.load(open(COUNTIES_FILE))['count']
                      if os.path.exists(COUNTIES_FILE) else 0)
    print(f"Service state ({STATE_FILE}):")
    print(f"  started:        {s.get('started_at')}")
    print(f"  last update:    {s.get('updated_at')}")
    print(f"  phase:          {s.get('phase')}")
    print(f"  last county:    {s.get('last_county')}")
    print(f"  phase 1 done:   {len(s.get('phase1_done', []))}/{counties_total}")
    print(f"  phase 2 done:   {len(s.get('phase2_done', []))}/{counties_total}")
    print(f"  errors logged:  {len(s.get('errors', []))}")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--interval', type=int, default=60,
                   help='base seconds between phase-2 county scrapes (jittered ±50%%; default 60)')
    p.add_argument('--limit', type=int, default=None,
                   help='cap parcels per county (useful for overnight coverage runs)')
    p.add_argument('--workers', type=int, default=4,
                   help='concurrent detail/shape workers within a county (default 4)')
    p.add_argument('--skip-phase1', action='store_true',
                   help='skip the list-only sweep and go straight to enrichment')
    p.add_argument('--status', action='store_true', help='print state and exit')
    args = p.parse_args(argv)

    if args.status:
        return cmd_status()

    if not os.path.exists(COUNTIES_FILE):
        log("counties.json missing — fetching first")
        cmd_counties()
    counties = json.load(open(COUNTIES_FILE))['counties']

    state = load_state()

    def _on_signal(signum, frame):
        log(f"Received signal {signum} — exiting (state already saved).")
        sys.exit(0)
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    log(f"Service starting. State: {STATE_FILE}")
    log(f"  Phase 1: {len(state['phase1_done'])}/{len(counties)} done")
    log(f"  Phase 2: {len(state['phase2_done'])}/{len(counties)} done")

    try:
        if state['phase'] == 'list' and not args.skip_phase1:
            phase1_list_only(counties, state, limit=args.limit)
        if state['phase'] != 'done':
            phase2_enrich(counties, state, interval=args.interval,
                          limit=args.limit, workers=args.workers)
    except KeyboardInterrupt:
        log("Interrupted — state saved, exit clean.")
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
