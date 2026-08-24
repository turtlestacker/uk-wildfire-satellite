"""Filter raw detections down to qualifying wildfire hotspots.

Each step records how many rows it removed so the funnel can be published.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
from sklearn.neighbors import KDTree

from . import config
from .boundary import points_in_uk, to_metric

log = logging.getLogger(__name__)

PERSISTENT_GEOJSON = config.STATIC_DIR / "persistent_sources.geojson"
MANUAL_EXCLUSIONS = config.STATIC_DIR / "manual_exclusions.csv"


def load_exclusion_points() -> pd.DataFrame:
    """Persistent heat sources (derived + manual) as a DataFrame of lat, lon, radius_m, name."""
    rows = []
    if PERSISTENT_GEOJSON.exists():
        gj = json.loads(PERSISTENT_GEOJSON.read_text())
        for f in gj["features"]:
            lon, lat = f["geometry"]["coordinates"]
            p = f.get("properties", {})
            rows.append({"lat": lat, "lon": lon, "radius_m": p.get("radius_m", config.PERSISTENT_EXCLUDE_RADIUS_M),
                         "name": p.get("name", ""), "origin": "derived"})
    if MANUAL_EXCLUSIONS.exists():
        m = pd.read_csv(MANUAL_EXCLUSIONS)
        for r in m.itertuples():
            rows.append({"lat": r.lat, "lon": r.lon, "radius_m": getattr(r, "radius_m", config.PERSISTENT_EXCLUDE_RADIUS_M),
                         "name": r.name, "origin": "manual"})
    return pd.DataFrame(rows, columns=["lat", "lon", "radius_m", "name", "origin"])


def near_exclusions(lat, lon, exclusions: pd.DataFrame) -> np.ndarray:
    """Boolean mask: within each exclusion point's radius."""
    lat = np.asarray(lat, dtype=float)
    if exclusions.empty or len(lat) == 0:
        return np.zeros(len(lat), dtype=bool)
    x, y = to_metric(lat, lon)
    ex, ey = to_metric(exclusions["lat"].values, exclusions["lon"].values)
    tree = KDTree(np.c_[ex, ey])
    rmax = float(exclusions["radius_m"].max())
    ind, dist = tree.query_radius(np.c_[x, y], r=rmax, return_distance=True)
    radii = exclusions["radius_m"].values.astype(float)
    mask = np.zeros(len(lat), dtype=bool)
    for i, (ii, dd) in enumerate(zip(ind, dist)):
        if len(ii) and np.any(dd <= radii[ii]):
            mask[i] = True
    return mask


def apply_filters(df: pd.DataFrame, exclusions: pd.DataFrame | None = None,
                  confidence_keep: set[str] = config.CONFIDENCE_KEEP) -> tuple[pd.DataFrame, dict]:
    """Return (filtered detections, funnel counts)."""
    funnel = {"bbox": int(len(df))}
    if exclusions is None:
        exclusions = load_exclusion_points()

    on_land = points_in_uk(df["latitude"].values, df["longitude"].values)
    df = df[on_land]
    funnel["uk_land"] = int(len(df))

    t = df["type"]
    df = df[~t.isin(list(config.DROP_TYPES)).fillna(False).astype(bool)]
    funnel["not_static_type"] = int(len(df))

    df = df[df["confidence"].isin(confidence_keep)]
    funnel["confidence"] = int(len(df))

    if len(df):
        df = df[~near_exclusions(df["latitude"].values, df["longitude"].values, exclusions)]
    funnel["not_persistent"] = int(len(df))

    log.info("filter funnel: %s", funnel)
    return df.reset_index(drop=True), funnel
