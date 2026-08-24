"""Daily / monthly / annual aggregation of detections and events."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from . import config


def daily_table(det: pd.DataFrame, events: pd.DataFrame, start: date, end: date,
                window: int = config.ROLLING_WINDOW_DAYS) -> pd.DataFrame:
    """One row per calendar day from start to end (zero-filled).

    det must carry an `event_id` column (from cluster.assign_events)."""
    idx = pd.date_range(start, end, freq="D")
    out = pd.DataFrame(index=idx)
    out.index.name = "date"
    if len(det):
        d = det.copy()
        d["acq_date"] = pd.to_datetime(d["acq_date"])
        g = d.groupby("acq_date")
        out["detections"] = g.size()
        for sat in ("SNPP", "NOAA20", "NOAA21"):
            out[f"detections_{sat.lower()}"] = d[d["sat"] == sat].groupby("acq_date").size()
        out["active_events"] = g["event_id"].nunique()
        out["frp_sum"] = g["frp"].sum().round(1)
        prod = g["product"].agg(lambda s: "SP" if set(s) == {"SP"} else ("NRT" if set(s) == {"NRT"} else "mixed"))
        out["product_status"] = prod
    else:
        for c in ("detections", "detections_snpp", "detections_noaa20", "detections_noaa21", "active_events", "frp_sum"):
            out[c] = 0
        out["product_status"] = ""
    if len(events):
        ns = pd.to_datetime(events["start_date"]).value_counts()
        out["new_events"] = ns
    else:
        out["new_events"] = 0
    num = ["detections", "detections_snpp", "detections_noaa20", "detections_noaa21", "active_events", "frp_sum", "new_events"]
    out[num] = out[num].fillna(0)
    for c in num:
        if c != "frp_sum":
            out[c] = out[c].astype(int)
    out["product_status"] = out["product_status"].fillna("")
    out["active_events_7d"] = out["active_events"].rolling(window, min_periods=1).mean().round(2)
    out["detections_7d"] = out["detections"].rolling(window, min_periods=1).mean().round(2)
    return out.reset_index()


def monthly_table(daily: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    d = daily.copy()
    d["year"] = d["date"].dt.year
    d["month"] = d["date"].dt.month
    m = d.groupby(["year", "month"]).agg(detections=("detections", "sum"),
                                         event_days=("active_events", "sum"),
                                         new_events=("new_events", "sum"),
                                         frp_sum=("frp_sum", "sum"),
                                         days=("date", "size")).reset_index()
    if len(events):
        e = events.copy()
        e["start"] = pd.to_datetime(e["start_date"])
        e["year"], e["month"] = e["start"].dt.year, e["start"].dt.month
        big = e.groupby(["year", "month"])["n_detections"].max().rename("largest_event_detections")
        m = m.merge(big.reset_index(), on=["year", "month"], how="left")
        m["largest_event_detections"] = m["largest_event_detections"].fillna(0).astype(int)
    m["frp_sum"] = m["frp_sum"].round(1)
    return m


def annual_table(daily: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    d = daily.copy()
    d["year"] = d["date"].dt.year
    a = d.groupby("year").agg(detections=("detections", "sum"),
                              event_days=("active_events", "sum"),
                              new_events=("new_events", "sum"),
                              frp_sum=("frp_sum", "sum"),
                              days_with_activity=("active_events", lambda s: int((s > 0).sum())),
                              peak_active_events=("active_events", "max"),
                              days=("date", "size")).reset_index()
    peak_day = d.loc[d.groupby("year")["active_events"].idxmax(), ["year", "date"]].rename(columns={"date": "peak_day"})
    a = a.merge(peak_day, on="year")
    a["peak_day"] = a["peak_day"].dt.date.astype(str)
    if len(events):
        e = events.copy()
        e["year"] = pd.to_datetime(e["start_date"]).dt.year
        a = a.merge(e.groupby("year")["n_detections"].max().rename("largest_event_detections").reset_index(),
                    on="year", how="left")
        a = a.merge(e.groupby("year")["duration_days"].max().rename("longest_event_days").reset_index(),
                    on="year", how="left")
        a["largest_event_detections"] = a["largest_event_detections"].fillna(0).astype(int)
        a["longest_event_days"] = a["longest_event_days"].fillna(0).astype(int)
    a["frp_sum"] = a["frp_sum"].round(1)
    # every year before the last one in the table has been fully fetched (2012 starts on 20 Jan)
    a["complete"] = a["year"] < a["year"].max()
    return a


def cumulative_by_doy(daily: pd.DataFrame, column: str = "new_events") -> pd.DataFrame:
    """Wide table: rows = day of year (1..366), columns = year, values = cumulative sum."""
    d = daily.copy()
    d["year"] = d["date"].dt.year
    d["doy"] = d["date"].dt.dayofyear
    d["cum"] = d.groupby("year")[column].cumsum()
    wide = d.pivot(index="doy", columns="year", values="cum")
    return wide.ffill().fillna(0).astype(int)


def same_period_stats(daily: pd.DataFrame, today: date, column: str = "new_events") -> dict:
    """Year-to-date total this year vs the mean over prior complete years for the same period."""
    d = daily.copy()
    d["year"] = d["date"].dt.year
    d["doy"] = d["date"].dt.dayofyear
    doy = pd.Timestamp(today).dayofyear
    ytd = d[(d["year"] == today.year) & (d["doy"] <= doy)][column].sum()
    prior = d[(d["year"] < today.year) & (d["doy"] <= doy)].groupby("year")[column].sum()
    prior = prior[prior.index >= d["year"].min() + (1 if pd.Timestamp(daily["date"].min()).dayofyear > 5 else 0)]
    return {"ytd": int(ytd), "prior_mean": float(prior.mean().round(1)) if len(prior) else None,
            "prior_max": int(prior.max()) if len(prior) else None,
            "prior_max_year": int(prior.idxmax()) if len(prior) else None,
            "prior_years": [int(y) for y in prior.index],
            "rank": int((prior > ytd).sum() + 1) if len(prior) else None}
