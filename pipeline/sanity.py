"""Real-data sanity checks against well-known UK fire episodes and industrial sites.

    python -m pipeline.sanity

Prints PASS/WARN lines; exits non-zero if a hard check fails. Requires a completed build.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from . import config

# (name, lat, lon, start, end) — episodes that must show up as events
EPISODES = [
    ("Saddleworth Moor 2018", 53.53, -1.98, "2018-06-24", "2018-07-20"),
    ("Winter Hill 2018", 53.63, -2.53, "2018-06-28", "2018-07-20"),
    ("Marsden Moor Apr 2019", 53.60, -1.93, "2019-04-20", "2019-04-30"),
    ("Wennington / London 19 Jul 2022", 51.52, 0.23, "2022-07-19", "2022-07-20"),
    ("Cannich, Highlands May 2023", 57.33, -4.80, "2023-05-27", "2023-06-05"),
]
# moorland / heath areas that must NOT be flagged as persistent sources
WILDFIRE_AREAS = [("Saddleworth Moor", 53.53, -1.98), ("Marsden Moor", 53.60, -1.93), ("Winter Hill", 53.63, -2.53),
                  ("Bleaklow / Kinder", 53.45, -1.87), ("Dartmoor", 50.58, -3.95), ("Ashdown Forest", 51.07, 0.05)]
# industrial sites that must be in the exclusion list
INDUSTRIAL = [("Grangemouth", 56.02, -3.70), ("Fawley", 50.83, -1.34), ("Port Talbot", 51.565, -3.775),
              ("Scunthorpe", 53.59, -0.62), ("Drax", 53.736, -0.99), ("Pembroke", 51.685, -4.99)]


def km(lat1, lon1, lat2, lon2):
    return np.hypot((lat1 - lat2) * 111.2, (lon1 - lon2) * 111.2 * np.cos(np.radians(lat1)))


def main() -> int:
    ok = True
    events = pd.read_csv(config.PROCESSED_DIR / "events.csv", parse_dates=["start_date", "end_date"])
    daily = pd.read_csv(config.PROCESSED_DIR / "daily.csv", parse_dates=["date"]).set_index("date")
    ex = json.loads((config.STATIC_DIR / "persistent_sources.geojson").read_text())["features"]
    exdf = pd.DataFrame([{"name": f["properties"]["name"], "lon": f["geometry"]["coordinates"][0],
                          "lat": f["geometry"]["coordinates"][1], "radius_m": f["properties"]["radius_m"]} for f in ex])
    manual = config.STATIC_DIR / "manual_exclusions.csv"
    if manual.exists():
        exdf = pd.concat([exdf, pd.read_csv(manual)[["name", "lat", "lon", "radius_m"]]], ignore_index=True)

    print("== episodes ==")
    for name, lat, lon, s, e in EPISODES:
        near = events[(km(events.centroid_lat, events.centroid_lon, lat, lon) < 8) &
                      (events.end_date >= s) & (events.start_date <= e)]
        if near.empty:
            print(f"FAIL {name}: no event within 8 km in {s}..{e}"); ok = False
        else:
            big = near.sort_values("n_detections", ascending=False).iloc[0]
            print(f"PASS {name}: {len(near)} event(s); largest {big.n_detections} detections, "
                  f"{big.start_date.date()}..{big.end_date.date()} ({big.duration_days} d), max FRP {big.max_frp}")

    print("== notable days ==")
    for day, label in [("2022-07-19", "40 C day"), ("2018-06-27", "Saddleworth peak"), ("2025-04-05", "spring 2025 fire wave")]:
        if pd.Timestamp(day) in daily.index:
            r = daily.loc[day]
            print(f"INFO {day} ({label}): {r.detections} detections, {r.active_events} active events, 7d avg {r.active_events_7d}")
    y = daily.groupby(daily.index.year)["new_events"].sum()
    print("INFO events started per year:\n" + y.to_string())
    spring25 = daily.loc["2025-03-01":"2025-04-30", "new_events"].sum()
    spring_prior = daily.loc["2012-01-01":"2024-12-31"]
    prior = spring_prior[(spring_prior.index.month.isin([3, 4]))].groupby(spring_prior[(spring_prior.index.month.isin([3, 4]))].index.year)["new_events"].sum()
    print(f"INFO Mar-Apr 2025 new events {spring25} vs prior Mar-Apr mean {prior.mean():.0f} (max {prior.max()} in {prior.idxmax()})")
    if spring25 < prior.mean():
        print("WARN spring 2025 not above the prior average — reported as a record season")

    print("== persistent sources ==")
    for name, lat, lon in INDUSTRIAL:
        d = km(exdf.lat, exdf.lon, lat, lon)
        if (d < 4).any():
            print(f"PASS {name} excluded ({exdf.loc[d.idxmin(), 'name']}, {d.min():.1f} km)")
        else:
            print(f"FAIL {name} not in exclusion list"); ok = False
    for name, lat, lon in WILDFIRE_AREAS:
        d = km(exdf.lat, exdf.lon, lat, lon)
        if (d < 3).any():
            print(f"FAIL {name} is flagged as a persistent source ({exdf.loc[d.idxmin(), 'name']}, {d.min():.1f} km)"); ok = False
        else:
            print(f"PASS {name} not flagged")
    print("OK" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
