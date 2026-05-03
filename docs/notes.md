# Data Sources

Reference for every external endpoint the scraper hits. Source code in
[src/govease.py](../src/govease.py).

## GovEase

The auction host. Public, no key, no login required for the pages we read.

### County list
Used by `scrape.py counties` to seed `data/counties.json`.

```
GET https://www.govease.com/auctions
```
HTML page; we parse the embedded county dropdown.

### Parcel list (per county)
```
GET https://liveauctions.govease.com/auction/{county_slug}/{auction_id}/items
```
Returns the lot table — `lot_id`, `parcel_slug`, `parcel_number`, `owner`,
`face_value`, `auction_name`, `auction_type`.

### Parcel detail
```
GET https://liveauctions.govease.com/auction/{county_slug}/{auction_id}/items/{parcel_slug}
```
HTML page with `physical_address`, `legal_description`, `assessed_value`,
`true_value`, `true_land`, `true_building`, `tax_year`, `unique_number`,
`primary_owner`, mailing address.

The page also embeds an inline `displayAuctionShapeData(...)` JS call that
reveals the **canonical shape slug** for the county — sometimes different from
the URL slug (e.g. `aljeffersonbessemer` → `jefferson`). When a new mismatch
turns up, add it to `_SHAPE_COUNTY_OVERRIDES` in `govease.py`.

### Parcel shape (lat/lng + polygon)
```
GET https://liveauctions.govease.com/api/auction-shape/{shape_slug}/{parcel_number}
```
Returns the parcel polygon as `[[[lng, lat], …]]` plus a centroid. This is
what GovEase itself displays — authoritative, no third-party geocoder needed.

## Auction schedule

Sale dates, registration deadlines, pre-bidding windows, sale types.

```
https://www.govease.com/tax-sale-property-auctions
```
GovEase serves this through an Awesome Table widget that doesn't expose a JSON
endpoint. Workflow:

1. Visit https://view-awesome-table.com/-MF6O6OFpMxeDFAjyNJg/view, copy rows.
2. Paste into the `RAW` constant at the top of
   [tools/import-schedule.py](../tools/import-schedule.py).
3. Run `python tools/import-schedule.py` — regenerates
   `data/auction-schedule.json`, keyed by `county_id`.

## Politeness

The scraper imitates a polite human:

| Operation | Delay |
|---|---|
| Detail fetch | 2–5 s jittered |
| Shape fetch | 1–2.5 s jittered |
| County → county (in `all`) | 10–30 s |
| User-Agent | rotated from a 3-string pool |
| County order in `all` | shuffled per run |

A full sweep across all 113 counties takes hours-to-days. Use `service.py` for
long runs — it saves state per-county and is safe to kill.
