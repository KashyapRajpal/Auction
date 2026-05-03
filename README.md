# Auction Dashboard

A Zillow-style browser for US tax-lien and tax-deed auctions, sourced from
[GovEase](https://liveauctions.govease.com) — 113 counties across AL, AZ, CA,
CO, GA, IA, IN, LA, MS, OK, PA, RI, TN, TX, and WA.

The dashboard is a static HTML page with no backend. The scraper writes
per-county JSON snapshots into `data/snapshots/`; the page reads them as static
files. A scheduled GitHub Actions workflow re-runs the scraper every 15 days
and commits any updated snapshots. User clicks never write artifacts.

## Layout

```
.
├── index.html                  ← static dashboard, served from repo root for GitHub Pages
├── data/
│   ├── counties.json           ← committed; full list of GovEase counties
│   ├── manifest.json           ← committed; lists which counties have snapshots
│   ├── auction-schedule.json   ← committed; sale dates / registration deadlines
│   └── snapshots/              ← committed; one *.json per scraped county
├── src/
│   ├── scrape.py               ← CLI orchestration (counties / county / all)
│   ├── govease.py              ← HTTP client + HTML/JSON parsers
│   └── service.py              ← long-running, resumable scraper service
├── tools/
│   ├── import-schedule.py      ← regenerates auction-schedule.json from pasted data
│   └── fix-latlng.py           ← retroactive lat/lng backfill for legacy snapshots
├── docs/
│   └── notes.md                ← data-source reference
├── .github/workflows/scrape.yml ← scheduled scrape every 15 days
└── README.md
```

Snapshots in `data/snapshots/` *are* committed because they are produced by CI
/ the scraper, not by user clicks. Runtime state (`data/service-state.json`)
and editor / OS cruft are gitignored.

## Quick start

### 1. Install dependencies

```bash
pip install requests
```

### 2. Scrape a county

```bash
# Refresh the county list (only needed once, or after GovEase adds counties)
python src/scrape.py counties

# Smoke test — 5 parcels with full enrichment (~30 seconds)
python src/scrape.py county AL albarbour 1262 --limit 5

# Full county scrape (Barbour: ~500 parcels, ~25 minutes)
python src/scrape.py county AL albarbour 1262

# Skip detail fetches (fast, list-only data)
python src/scrape.py county AL albarbour 1262 --no-details --no-geocode

# Scrape every county in counties.json (long; for CI / overnight runs)
python src/scrape.py all
```

Each county scrape writes `data/snapshots/{STATE}-{slug}.json` and updates
`data/manifest.json` so the dashboard knows the county now has data.

### 3. View the dashboard

`fetch()` doesn't work from `file://` in Chrome/Firefox, so serve over HTTP:

```bash
python -m http.server 8765
# open http://localhost:8765
```

Pick a county from the dropdown — counties with `· N` next to their name have
snapshot data; counties marked `· no data` do not (run the scraper to populate
them).

## What the scraper captures

For each parcel, the snapshot JSON includes:

| Field | Source | Notes |
|---|---|---|
| `lot_id`, `parcel_slug`, `parcel_number` | list page | identifiers |
| `owner`, `face_value` | list page | face value = minimum bid |
| `auction_name`, `auction_type` | list page | e.g. "Tax Lien" |
| `physical_address` | detail page | property location, often blank in AL |
| `legal_description` | detail page | metes-and-bounds text |
| `assessed_value`, `true_value`, `true_land`, `true_building` | detail page | dollars |
| `tax_year`, `unique_number` | detail page | |
| `primary_owner`, `mailing_street`, `mailing_city`, `mailing_state`, `mailing_zip` | detail page | owner of record + their mailing address |
| `lat`, `lng` | shape API | authoritative — same coords GovEase displays |
| `polygon` | shape API | parcel boundary as `[[[lng,lat], …]]` (GeoJSON-compatible) |

Every visible field on a GovEase parcel page is captured, plus the parcel
boundary polygon. The dashboard's detail panel shows all of it inline so users
do not have to leave for GovEase.

## Politeness

The scraper is built to look like a polite human browsing:

- 2-5 s jittered delay between detail fetches
- 1-2.5 s between shape fetches
- 10-30 s between counties when running `all`
- Counties shuffled on each `all` run (no consistent traffic pattern)
- User-Agent rotated from a 3-string pool

A full `all` run takes hours-to-days depending on parcel counts. For background
running, use `src/service.py` — it scrapes in two phases (list-only sweep,
then full enrichment), saves state per-county, and is safe to kill at any
time. Examples:

```bash
python src/service.py                   # full run with default pacing
python src/service.py --interval 60     # 60s gap between phase-2 counties
python src/service.py --limit 100       # cap parcels per county
python src/service.py --status          # print state, exit
```

Runtime state lives in `data/service-state.json` (gitignored). Once every
county is enriched, the file can be deleted — a fresh run rebuilds it.

## Fixing under-geocoded snapshots

GovEase's shape API uses a county slug that occasionally differs from the URL
slug (e.g. `aljeffersonbessemer` → `jefferson`, `alstclair` → `st. clair`,
`alwinstin` → `winston`). When a county's parcels show in the cards list but
not on the map, run:

```bash
python tools/fix-latlng.py                  # scan all snapshots, fix any <50% covered
python tools/fix-latlng.py --threshold 0.9  # stricter — re-run anything <90%
python tools/fix-latlng.py --county AL aljeffersonbessemer 1312    # one county only
python tools/fix-latlng.py --dry-run        # report what would be fixed
```

It auto-discovers the canonical shape slug from a single parcel detail page
(by reading the inline `displayAuctionShapeData(...)` JS call), then reshapes
every missing-lat parcel in parallel. Authoritative — no guessing.

When you find a new mismatch, also add it to `_SHAPE_COUNTY_OVERRIDES` in
`src/govease.py` so future scrapes get it right on the first pass.

## Auction schedule

`data/auction-schedule.json` holds sale dates, registration deadlines, pre-bidding
times, and sale types — sourced from the public schedule at
[govease.com/tax-sale-property-auctions](https://www.govease.com/tax-sale-property-auctions).
GovEase publishes this through an Awesome Table widget that doesn't expose JSON,
so we paste the table into `tools/import-schedule.py` and regenerate:

```bash
# 1. visit https://view-awesome-table.com/-MF6O6OFpMxeDFAjyNJg/view, copy the rows
# 2. paste into the RAW string at the top of tools/import-schedule.py
# 3. regenerate
python tools/import-schedule.py
```

The JSON is keyed by `county_id` so the dashboard joins it automatically. Counties
without a schedule entry simply hide the banner.

## CI

[.github/workflows/scrape.yml](.github/workflows/scrape.yml) runs
`scrape.py all` on the 1st and 16th of each month (~every 15 days) and commits
any updated snapshots back to the repo. The workflow can also be triggered
manually from the Actions tab. Each run is bounded to 5 hours via `timeout`
so the GitHub Actions 6-hour job limit can't kill it mid-county — snapshots
are written incrementally, so a timeout just means the next run picks up
where this one left off.

(CI uses `scrape.py` rather than `service.py` because the runner is fresh
each time. `service.py`'s phased + resumable design is a win for long-running
laptop sessions but pure overhead when there's no state to resume from.)

See [docs/notes.md](docs/notes.md) for the data-source reference.
