"""Cluster hotspot detections into fire events using space-time single linkage.

Two detections belong to the same event if they are within `eps_m` of each other
and observed within `max_gap_days` of each other (transitively). This treats several
pixels of one fire, repeat overpasses by different satellites, and consecutive-day
observations as a single event, while tolerating a cloudy day in between.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.neighbors import KDTree

from . import config
from .boundary import to_metric

log = logging.getLogger(__name__)


class _UnionFind:
    def __init__(self, n: int):
        self.parent = np.arange(n)

    def find(self, i: int) -> int:
        p = self.parent
        while p[i] != i:
            p[i] = p[p[i]]
            i = p[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def assign_events(df: pd.DataFrame, eps_m: float = config.CLUSTER_EPS_M,
                  max_gap_days: int = config.CLUSTER_MAX_GAP_DAYS) -> np.ndarray:
    """Return an integer event label per row of `df` (needs latitude, longitude, acq_date)."""
    n = len(df)
    if n == 0:
        return np.array([], dtype=int)
    x, y = to_metric(df["latitude"].values, df["longitude"].values)
    day = pd.to_datetime(df["acq_date"]).map(pd.Timestamp.toordinal).values.astype(int)
    order = np.argsort(day, kind="stable")
    x, y, day = x[order], y[order], day[order]
    uf = _UnionFind(n)

    # Process day by day; link each day's points to points in the preceding `max_gap_days` days
    # and to themselves.
    unique_days = np.unique(day)
    starts = np.searchsorted(day, unique_days, side="left")
    ends = np.searchsorted(day, unique_days, side="right")
    for k, d in enumerate(unique_days):
        idx = np.arange(starts[k], ends[k])
        pts = np.c_[x[idx], y[idx]]
        tree = KDTree(pts)
        # same-day links
        for i_local, neigh in enumerate(tree.query_radius(pts, r=eps_m)):
            for j_local in neigh:
                if j_local > i_local:
                    uf.union(idx[i_local], idx[j_local])
        # links back to previous days within the gap
        lo = np.searchsorted(day, d - max_gap_days, side="left")
        prev = np.arange(lo, starts[k])
        if len(prev):
            prev_pts = np.c_[x[prev], y[prev]]
            for i_local, neigh in enumerate(tree.query_radius(prev_pts, r=eps_m)):
                for j_local in neigh:
                    uf.union(prev[i_local], idx[j_local])

    roots = np.array([uf.find(i) for i in range(n)])
    # relabel 0..k-1 in order of first appearance (sorted by day), then map back to input order
    _, labels_sorted = np.unique(roots, return_inverse=True)
    first_seen = {}
    relabel = np.empty(n, dtype=int)
    nxt = 0
    for i in range(n):
        r = labels_sorted[i]
        if r not in first_seen:
            first_seen[r] = nxt
            nxt += 1
        relabel[i] = first_seen[r]
    labels = np.empty(n, dtype=int)
    labels[order] = relabel
    return labels


def summarise_events(df: pd.DataFrame, labels: np.ndarray) -> pd.DataFrame:
    """One row per event with dates, size, location and intensity."""
    d = df.copy()
    d["event_id"] = labels
    d["acq_date"] = pd.to_datetime(d["acq_date"])
    g = d.groupby("event_id")
    ev = pd.DataFrame({
        "start_date": g["acq_date"].min().dt.date,
        "end_date": g["acq_date"].max().dt.date,
        "n_days_detected": g["acq_date"].nunique(),
        "n_detections": g.size(),
        "centroid_lat": g["latitude"].mean().round(5),
        "centroid_lon": g["longitude"].mean().round(5),
        "min_lat": g["latitude"].min(), "max_lat": g["latitude"].max(),
        "min_lon": g["longitude"].min(), "max_lon": g["longitude"].max(),
        "max_frp": g["frp"].max().round(2),
        "sum_frp": g["frp"].sum().round(2),
        "satellites": g["sat"].agg(lambda s: "+".join(sorted(set(s)))),
        "n_high_conf": g["confidence"].agg(lambda s: int((s == "h").sum())),
        "n_night": g["daynight"].agg(lambda s: int((s == "N").sum())),
    }).reset_index()
    ev["duration_days"] = (pd.to_datetime(ev["end_date"]) - pd.to_datetime(ev["start_date"])).dt.days + 1
    return ev.sort_values(["start_date", "event_id"]).reset_index(drop=True)
