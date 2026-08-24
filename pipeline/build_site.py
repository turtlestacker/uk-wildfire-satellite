"""Write the JSON payloads consumed by the static website."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import aggregate, config
from .filters import load_exclusion_points

log = logging.getLogger(__name__)


def _dump(name: str, obj) -> None:
    config.SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.SITE_DATA_DIR / name
    path.write_text(json.dumps(_clean(obj), separators=(",", ":"), default=_json_default, allow_nan=False))
    log.info("wrote %s (%.0f kB)", path.name, path.stat().st_size / 1024)


def _clean(o):
    """Recursively replace NaN/NaT with None so the output is strict JSON."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, float) and np.isnan(o):
        return None
    if o is pd.NaT:
        return None
    return o


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, pd.Timestamp):
        return o.date().isoformat()
    raise TypeError(type(o))


def write_all(raw, det, events, daily, monthly, annual, funnel, end: date) -> None:
    # --- daily series (columnar) -------------------------------------------------------------
    cols = ["detections", "detections_snpp", "detections_noaa20", "detections_noaa21", "active_events",
            "new_events", "frp_sum", "active_events_7d", "detections_7d", "product_status"]
    _dump("daily.json", {"dates": daily["date"].dt.strftime("%Y-%m-%d").tolist(),
                         **{c: daily[c].tolist() for c in cols}})

    # --- all events, light --------------------------------------------------------------------
    ev = events
    _dump("events_all.json", {
        "id": ev["event_id"].tolist(),
        "start": pd.to_datetime(ev["start_date"]).dt.strftime("%Y-%m-%d").tolist(),
        "end": pd.to_datetime(ev["end_date"]).dt.strftime("%Y-%m-%d").tolist(),
        "lat": ev["centroid_lat"].tolist(), "lon": ev["centroid_lon"].tolist(),
        "n": ev["n_detections"].tolist(), "days": ev["n_days_detected"].tolist(),
        "max_frp": ev["max_frp"].tolist(), "sum_frp": ev["sum_frp"].tolist(),
    })

    # --- recent detections for the map -------------------------------------------------------
    cutoff = pd.Timestamp(end - timedelta(days=config.RECENT_EVENT_DAYS))
    d = det[pd.to_datetime(det["acq_date"]) >= cutoff]
    _dump("detections_recent.json", {
        "date": pd.to_datetime(d["acq_date"]).dt.strftime("%Y-%m-%d").tolist(),
        "time": d["acq_time"].astype(int).tolist(),
        "lat": d["latitude"].round(5).tolist(), "lon": d["longitude"].round(5).tolist(),
        "frp": d["frp"].round(1).tolist(), "conf": d["confidence"].tolist(),
        "sat": d["sat"].tolist(), "night": (d["daynight"] == "N").astype(int).tolist(),
        "event": d["event_id"].tolist(),
    })

    # --- tables -------------------------------------------------------------------------------
    _dump("annual.json", annual.to_dict(orient="records"))
    _dump("monthly.json", monthly.to_dict(orient="records"))
    cum = aggregate.cumulative_by_doy(daily, "new_events")
    _dump("cumulative.json", {"doy": cum.index.tolist(), "years": {str(y): cum[y].tolist() for y in cum.columns}})
    cum_d = aggregate.cumulative_by_doy(daily, "detections")
    _dump("cumulative_detections.json", {"doy": cum_d.index.tolist(),
                                         "years": {str(y): cum_d[y].tolist() for y in cum_d.columns}})

    # --- downloadable CSVs ----------------------------------------------------------------------
    config.SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    daily.to_csv(config.SITE_DATA_DIR / "daily.csv", index=False)
    events.to_csv(config.SITE_DATA_DIR / "events.csv", index=False)

    # --- persistent sources -------------------------------------------------------------------
    ex = load_exclusion_points()
    _dump("persistent_sources.json", ex.to_dict(orient="records"))

    # --- meta / headline numbers ---------------------------------------------------------------
    last7 = daily[daily["date"] > pd.Timestamp(end - timedelta(days=7))]
    last30 = daily[daily["date"] > pd.Timestamp(end - timedelta(days=30))]
    top = events.sort_values(["n_detections", "sum_frp"], ascending=False).head(15)
    sens_path = config.PROCESSED_DIR / "clustering_sensitivity.csv"
    sensitivity = pd.read_csv(sens_path).to_dict(orient="records") if sens_path.exists() else []
    sp_max = {}
    nrt_max = {}
    for sat in ("SNPP", "NOAA20", "NOAA21"):
        s = raw[raw["sat"] == sat]
        if len(s[s["product"] == "SP"]):
            sp_max[sat] = str(s.loc[s["product"] == "SP", "acq_date"].max())
        if len(s[s["product"] == "NRT"]):
            nrt_max[sat] = str(s.loc[s["product"] == "NRT", "acq_date"].max())
    meta = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_start": config.HISTORY_START,
        "data_end": end.isoformat(),
        "sp_max_date": sp_max,
        "nrt_max_date": nrt_max,
        "funnel": funnel,
        "n_events": int(len(events)),
        "n_persistent_sources": int(len(ex)),
        "config": {
            "confidence_keep": sorted(config.CONFIDENCE_KEEP),
            "land_buffer_m": config.LAND_BUFFER_M,
            "cluster_eps_m": config.CLUSTER_EPS_M,
            "cluster_max_gap_days": config.CLUSTER_MAX_GAP_DAYS,
            "persistent_min_cal_months": config.PERSISTENT_MIN_CAL_MONTHS,
            "persistent_min_year_months": config.PERSISTENT_MIN_YEAR_MONTHS,
            "persistent_exclude_radius_m": config.PERSISTENT_EXCLUDE_RADIUS_M,
            "bbox": config.UK_BBOX,
        },
        "headline": {
            "events_last7": int(last7["active_events"].sum()),
            "new_events_last7": int(last7["new_events"].sum()),
            "detections_last7": int(last7["detections"].sum()),
            "new_events_last30": int(last30["new_events"].sum()),
            "detections_last30": int(last30["detections"].sum()),
            "avg7_active_events": float(daily["active_events_7d"].iloc[-1]),
            "ytd": aggregate.same_period_stats(daily, end, "new_events"),
            "ytd_detections": aggregate.same_period_stats(daily, end, "detections"),
        },
        "top_events": top.to_dict(orient="records"),
        "sensitivity": sensitivity,
    }
    _dump("meta.json", meta)
