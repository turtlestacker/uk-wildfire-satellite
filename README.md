# UK wildfire activity from satellite detections

A self-updating dataset and website estimating daily wildfire activity across the United
Kingdom from NASA FIRMS **VIIRS 375 m active-fire detections**, 2012 to present.

It distinguishes **satellite detections** (hotspot pixels) from **estimated fire events**
(space–time clusters of detections), restricts to UK land, removes persistent industrial heat
sources, and publishes daily / monthly / annual series with a map and year-on-year comparison.

> This is an *estimate of UK wildfire activity derived from satellite active-fire detections*,
> not an official count of wildfires. See [Caveats](#caveats).

## Layout

```
pipeline/            Python pipeline (fetch → filter → cluster → aggregate → site JSON)
tests/               pytest unit tests (synthetic clustering / filter / aggregation cases)
data/raw/            raw FIRMS rows per source and year (committed; UK volume is small)
data/static/         UK land polygon, persistent heat-source list, manual exclusions
data/processed/      filtered detections, events, daily/monthly/annual tables, sensitivity
site/                static website (ECharts + Leaflet, no build step) and its JSON payloads
.github/workflows/   daily update + GitHub Pages deploy; tests
```

## Method

1. **Source.** FIRMS area API, VIIRS 375 m: `VIIRS_SNPP_SP` (from 2012-01-20), `VIIRS_NOAA20_SP`
   (from 2018-04), `VIIRS_NOAA21_NRT` (from 2024-01), plus the NRT products to cover the months
   after the standard-processing (SP) archive ends. Where SP exists for a satellite/date, NRT rows
   for it are discarded. Bounding box `-8.7, 49.8, 1.95, 61.0`.
2. **UK land.** Natural Earth 10 m admin-0 `GBR` polygon (GB + NI + islands; Isle of Man and
   Channel Islands are separate features and excluded), buffered by 750 m in British National Grid
   so coastal fires whose pixel centre falls just offshore are kept.
3. **Static sources.** Rows with NASA `type` ∈ {1 volcano, 2 other static land source, 3 offshore}
   are dropped (SP only carries this column). Then a data-driven mask: 1 km grid cells with
   detections in ≥ 8 distinct calendar months, or ≥ 15 distinct (year, month) periods, are
   persistent heat sources (refineries, steelworks, power stations, flares). Adjacent cells merge
   into a site; detections within its exclusion radius (≥ 1 km, growing with footprint) are removed.
   `data/static/manual_exclusions.csv` adds known sites as a safety net. Recompute with
   `python -m pipeline.run derive-sources` (it is deliberately not recomputed daily). Besides
   industrial plants this also catches spots that are burned every year in the same season
   (agricultural / managed burning): these are repeated stationary heat sources, not wildfires,
   and are excluded too. `data/static/persistent_sources_review.csv` lists every flagged site
   with its statistics for inspection; known moorland wildfire areas are checked to be unflagged
   by `python -m pipeline.sanity`.
4. **Confidence.** Keep `n` (nominal) and `h` (high); drop `l` (low).
5. **Events.** Single-linkage clustering in space–time: two detections are in the same event if
   within **1,500 m** and within **2 days** of each other (transitively). Merges multiple pixels,
   repeat overpasses and multi-day burning; tolerates a cloudy day.
6. **Daily measures.** `detections` = qualifying hotspots; `active_events` = distinct events with
   ≥ 1 detection that day; `new_events` = events whose first detection was that day; 7-day means.

All thresholds are in `pipeline/config.py`. `python -m pipeline.run sensitivity` recomputes annual
event counts for eps ∈ {1000, 1500, 2000} m × gap ∈ {1, 2, 3} days
(`data/processed/clustering_sensitivity.csv`, also shown on the site).

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env            # add your FIRMS MAP_KEY (free: https://firms.modaps.eosdis.nasa.gov/api/map_key/)

python -m pipeline.run backfill          # one-off: full archive (~1,900 API calls, resumable)
python -m pipeline.run derive-sources    # persistent heat-source mask (commit the result)
python -m pipeline.run sensitivity       # clustering sensitivity table
python -m pipeline.run build             # filtered detections, events, daily tables, site JSON
python -m pipeline.run update            # daily: extend SP, refresh trailing NRT days

python -m pytest -q                      # unit tests
python -m http.server -d site 8000       # preview at http://localhost:8000
```

### Automation

`.github/workflows/update.yml` runs daily at 06:30 UTC: `update` → `build` → commit `data/` and
`site/data/` → deploy `site/` to GitHub Pages. It needs the repository secret `FIRMS_MAP_KEY`
and Pages set to "GitHub Actions" as the source.

### Numbers at the first full build (2012-01-20 to 2026-08-24)

| filter step | detections remaining |
|---|---:|
| in the UK bounding box | 240,600 |
| on UK land | 182,947 |
| not flagged static/offshore by NASA | 87,792 |
| nominal or high confidence | 85,820 |
| not near a persistent heat source | **66,045** |

Those 66,045 detections cluster into **23,933 fire events**. Events started per year range from
~930 (2014) to 3,180 (2025, the record); across the nine clustering settings in the sensitivity
grid the annual totals move by about ±5%.

## Outputs

`data/processed/daily.csv` (also `site/data/daily.csv`):

| column | meaning |
|---|---|
| `date` | UTC date |
| `detections`, `detections_snpp/noaa20/noaa21` | qualifying VIIRS hotspots |
| `active_events` | distinct fire events with a detection that day |
| `new_events` | fire events whose first detection was that day |
| `frp_sum` | sum of fire radiative power (MW) |
| `active_events_7d`, `detections_7d` | trailing 7-day means |
| `product_status` | SP / NRT / mixed — NRT days may change when SP arrives |

`events.csv`: one row per event (start/end, days detected, detections, centroid, bbox, FRP,
satellites). `annual.csv`, `monthly.csv`: totals; `clustering_sensitivity.csv`.

## Caveats

- VIIRS detects heat, not wildfires per se: agricultural and moorland management burns, bonfires,
  and industrial sources missed by the filters are included; small, short-lived or cloud-covered
  fires are missed. Each satellite passes over the UK about twice a day.
- Detection counts are not homogeneous over time (1 satellite until 2018, 2 from 2018, 3 from
  2024). Event counts are more robust; a Suomi NPP-only series is provided for consistency.
- The most recent months are NRT and are later replaced by the reprocessed SP product.
- Clustering thresholds are judgement calls — see the sensitivity table.

Data: NASA FIRMS / LANCE (VNP14IMGT, VJ114IMGT, VJ214IMGT). Boundary: Natural Earth.
Basemap tiles: © OpenStreetMap contributors, © CARTO.
