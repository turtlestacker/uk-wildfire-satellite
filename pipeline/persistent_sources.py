"""Derive persistent (non-wildfire) heat sources from the detection archive.

Refineries, steelworks, power stations, gas flares and some landfills are detected by
VIIRS all year round, including in the winter months when vegetation fires are rare.
We grid the archive at 1 km and flag cells that are detected in many distinct calendar
months and/or many distinct (year, month) periods. The result is committed and only
recomputed on demand so daily numbers do not shift silently.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
from pyproj import Transformer

from . import config
from .boundary import to_metric

log = logging.getLogger(__name__)

OUT_GEOJSON = config.STATIC_DIR / "persistent_sources.geojson"
OUT_REVIEW = config.STATIC_DIR / "persistent_sources_review.csv"

# Known industrial sites used to label derived clusters (and as a manual safety net).
KNOWN_SITES = [
    ("Grangemouth refinery/petrochemicals", 56.020, -3.700),
    ("Mossmorran NGL plant", 56.090, -3.320),
    ("Fawley refinery", 50.830, -1.340),
    ("Stanlow refinery", 53.280, -2.850),
    ("Pembroke refinery", 51.685, -4.990),
    ("Port Talbot steelworks", 51.565, -3.775),
    ("Scunthorpe steelworks", 53.590, -0.620),
    ("Drax power station", 53.736, -0.990),
    ("Wilton / Teesside industrial", 54.590, -1.130),
    ("Lindsey / Humber refineries", 53.635, -0.245),
    ("Ratcliffe-on-Soar power station", 52.865, -1.255),
    ("Ferrybridge / Eggborough", 53.715, -1.275),
    ("Milford Haven LNG / Dragon", 51.700, -5.020),
    ("Runcorn / Ince industrial", 53.300, -2.800),
    ("Saltend chemicals park", 53.735, -0.240),
    ("Shotton / Deeside industrial", 53.235, -3.030),
    ("Aberthaw power station", 51.385, -3.405),
    ("Sellafield", 54.420, -3.500),
    ("Sullom Voe oil terminal", 60.460, -1.257),
    ("St Fergus gas terminal", 57.580, -1.835),
    ("Ketton cement works", 52.641, -0.547),
    ("Hope cement works", 53.264, -1.856),
    ("Ribblesdale cement works, Clitheroe", 53.889, -2.382),
    ("Padeswood cement works", 53.151, -3.062),
    ("Celsa steelworks, Cardiff", 51.480, -3.137),
    ("Marchwood energy-from-waste", 50.919, -1.391),
    ("Lakeside energy-from-waste, Slough", 51.590, -0.597),
    ("Riverside energy-from-waste, Belvedere", 51.514, 0.105),
    ("Beddington energy-from-waste", 51.391, -0.168),
    ("Ballylumford / Kilroot", 54.850, -5.780),
    ("Tunstead cement / quarry", 53.270, -1.870),
    ("Cauldon cement works", 53.052, -1.905),
    ("Cemex Rugby cement works", 52.375, -1.283),
    ("Dunbar cement works", 55.985, -2.480),
    ("Ballyconnell cement (border)", 54.131, -7.579),
    ("Cottam / West Burton power stations", 53.335, -0.780),
]


def derive(df: pd.DataFrame, grid_m: int = config.PERSISTENT_GRID_M,
           min_cal_months: int = config.PERSISTENT_MIN_CAL_MONTHS,
           min_year_months: int = config.PERSISTENT_MIN_YEAR_MONTHS) -> pd.DataFrame:
    """Return a DataFrame of persistent-source clusters (lat, lon, radius_m, stats)."""
    d = df[["latitude", "longitude", "acq_date", "daynight", "type"]].copy()
    x, y = to_metric(d["latitude"].values, d["longitude"].values)
    d["cx"] = np.floor(x / grid_m).astype(int)
    d["cy"] = np.floor(y / grid_m).astype(int)
    dt = pd.to_datetime(d["acq_date"])
    d["month"] = dt.dt.month
    d["ym"] = dt.dt.year * 100 + dt.dt.month
    d["year"] = dt.dt.year
    d["night"] = (d["daynight"] == "N").astype(int)
    d["static_type"] = d["type"].isin([1, 2, 3]).fillna(False).astype(int)

    g = d.groupby(["cx", "cy"])
    cells = pd.DataFrame({
        "n": g.size(),
        "cal_months": g["month"].nunique(),
        "year_months": g["ym"].nunique(),
        "years": g["year"].nunique(),
        "night_share": g["night"].mean().round(2),
        "static_type_share": g["static_type"].mean().round(2),
        "lat": g["latitude"].mean(),
        "lon": g["longitude"].mean(),
    }).reset_index()
    flagged = cells[(cells["cal_months"] >= min_cal_months) | (cells["year_months"] >= min_year_months)].copy()
    log.info("persistent cells: %d of %d", len(flagged), len(cells))
    if flagged.empty:
        return pd.DataFrame(columns=["lat", "lon", "radius_m", "name", "n_cells", "n", "cal_months",
                                     "year_months", "years", "night_share"])

    # merge 8-connected adjacent cells into one site
    key = {(r.cx, r.cy): i for i, r in enumerate(flagged.itertuples())}
    parent = list(range(len(flagged)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for (cx, cy), i in key.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                j = key.get((cx + dx, cy + dy))
                if j is not None:
                    parent[find(i)] = find(j)
    flagged["site"] = [find(i) for i in range(len(flagged))]

    gs = flagged.groupby("site")
    sites = pd.DataFrame({
        "lat": gs.apply(lambda s: np.average(s["lat"], weights=s["n"])).round(5),
        "lon": gs.apply(lambda s: np.average(s["lon"], weights=s["n"])).round(5),
        "n_cells": gs.size(),
        "n": gs["n"].sum(),
        "cal_months": gs["cal_months"].max(),
        "year_months": gs["year_months"].max(),
        "years": gs["years"].max(),
        "night_share": gs.apply(lambda s: np.average(s["night_share"], weights=s["n"])).round(2),
        "static_type_share": gs.apply(lambda s: np.average(s["static_type_share"], weights=s["n"])).round(2),
    }).reset_index(drop=True)
    # exclusion radius grows with the site footprint (sqrt of cell count), never below the default
    sites["radius_m"] = np.maximum(config.PERSISTENT_EXCLUDE_RADIUS_M,
                                   (np.sqrt(sites["n_cells"]) * grid_m * 0.75).round(-2)).astype(int)
    sites["name"] = [nearest_known(la, lo) for la, lo in zip(sites["lat"], sites["lon"])]
    return sites.sort_values("n", ascending=False).reset_index(drop=True)


def nearest_known(lat: float, lon: float, max_km: float = 6.0) -> str:
    best, bd = "", max_km
    for name, kla, klo in KNOWN_SITES:
        dkm = np.hypot((lat - kla) * 111.2, (lon - klo) * 111.2 * np.cos(np.radians(lat)))
        if dkm < bd:
            best, bd = name, dkm
    return best or "unnamed persistent heat source"


def write(sites: pd.DataFrame) -> None:
    OUT_GEOJSON.parent.mkdir(parents=True, exist_ok=True)
    feats = []
    for r in sites.itertuples():
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [float(r.lon), float(r.lat)]},
                      "properties": {"name": r.name, "radius_m": int(r.radius_m), "n_detections": int(r.n),
                                     "n_cells": int(r.n_cells), "cal_months": int(r.cal_months),
                                     "year_months": int(r.year_months), "years": int(r.years),
                                     "night_share": float(r.night_share)}})
    OUT_GEOJSON.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, indent=1))
    sites.to_csv(OUT_REVIEW, index=False)
    log.info("wrote %d persistent sources to %s", len(sites), OUT_GEOJSON)
