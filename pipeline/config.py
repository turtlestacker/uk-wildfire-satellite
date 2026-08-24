"""Central configuration for the UK wildfire satellite pipeline.

Every tunable that affects the numbers lives here so the method is auditable.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
STATIC_DIR = DATA_DIR / "static"
PROCESSED_DIR = DATA_DIR / "processed"
SITE_DIR = ROOT / "site"
SITE_DATA_DIR = SITE_DIR / "data"

# ---------------------------------------------------------------------------
# FIRMS API
# ---------------------------------------------------------------------------
FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov"
FIRMS_AREA_URL = FIRMS_BASE + "/api/area/csv/{key}/{source}/{bbox}/{days}/{date}"
FIRMS_AVAILABILITY_URL = FIRMS_BASE + "/api/data_availability/csv/{key}/ALL"
FIRMS_KEY_STATUS_URL = FIRMS_BASE + "/mapserver/mapkey_status/?MAP_KEY={key}"
FIRMS_MAX_DAYS_PER_REQUEST = 5
FIRMS_TRANSACTION_LIMIT = 5000            # per 10-minute window
FIRMS_REQUEST_INTERVAL_S = 0.25           # gentle pacing (~4 req/s)

# Bounding box covering the whole UK incl. Shetland and Scilly: west,south,east,north
UK_BBOX = (-8.7, 49.8, 1.95, 61.0)
UK_BBOX_STR = ",".join(str(v) for v in UK_BBOX)

# VIIRS 375 m products. SP (standard processing) is the reprocessed archive and
# is preferred; NRT fills the gap between the SP max date and today.
SOURCES = {
    "VIIRS_SNPP_SP":   {"satellite": "N",  "product": "SP"},
    "VIIRS_SNPP_NRT":  {"satellite": "N",  "product": "NRT"},
    "VIIRS_NOAA20_SP": {"satellite": "1",  "product": "SP"},
    "VIIRS_NOAA20_NRT": {"satellite": "1", "product": "NRT"},
    "VIIRS_NOAA21_NRT": {"satellite": "2", "product": "NRT"},
}
SATELLITE_NAMES = {"N": "Suomi NPP", "1": "NOAA-20", "2": "NOAA-21"}
HISTORY_START = "2012-01-20"              # first VIIRS active-fire date

# On each daily update, re-fetch this many trailing days of NRT data because
# detections can arrive a day or two late.
NRT_REFRESH_DAYS = 10

# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------
CRS_METRIC = "EPSG:27700"                 # British National Grid (metres)
LAND_BUFFER_M = 750                       # keep coastal fires whose pixel centre is just offshore
CONFIDENCE_KEEP = {"n", "h"}              # drop low-confidence pixels
DROP_TYPES = {1, 2, 3}                    # volcano, other static land source, offshore (when column present)

# Persistent (non-wildfire) heat source derivation.
PERSISTENT_GRID_M = 1000
PERSISTENT_MIN_CAL_MONTHS = 8             # detected in >= 8 of the 12 calendar months across the record
PERSISTENT_MIN_YEAR_MONTHS = 15           # or in >= 15 distinct (year, month) periods
PERSISTENT_EXCLUDE_RADIUS_M = 1000

# ---------------------------------------------------------------------------
# Clustering detections into fire events
# ---------------------------------------------------------------------------
CLUSTER_EPS_M = 1500                      # detections closer than this may belong to the same fire
CLUSTER_MAX_GAP_DAYS = 2                  # ... if observed within this many days of each other
SENSITIVITY_EPS = (1000, 1500, 2000)
SENSITIVITY_GAPS = (1, 2, 3)

ROLLING_WINDOW_DAYS = 7
RECENT_EVENT_DAYS = 365                   # events included in the detailed site payload


def get_map_key() -> str:
    """Return the FIRMS MAP_KEY from the environment (.env is loaded if present)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:  # pragma: no cover
        pass
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not key:
        raise RuntimeError("FIRMS_MAP_KEY is not set (put it in .env or the environment)")
    return key
