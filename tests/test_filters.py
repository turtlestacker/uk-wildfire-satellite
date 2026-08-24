import numpy as np
import pandas as pd

from pipeline.boundary import points_in_uk
from pipeline.filters import apply_filters, near_exclusions
from tests.conftest import det, point


def test_points_in_uk_known_locations():
    pts = {"Snowdonia": (53.07, -3.95, True), "Belfast": (54.60, -5.93, True), "Lerwick": (60.15, -1.15, True),
           "Isle of Man": (54.23, -4.55, False), "Dublin": (53.35, -6.26, False), "Jersey": (49.21, -2.13, False),
           "North Sea": (56.0, 1.0, False), "Calais": (50.95, 1.85, False), "300m off Cornwall": (50.0405, -5.66, True)}
    lat = np.array([v[0] for v in pts.values()])
    lon = np.array([v[1] for v in pts.values()])
    res = points_in_uk(lat, lon)
    for (name, (_, _, expected)), got in zip(pts.items(), res):
        assert bool(got) == expected, name


def test_confidence_and_type_filters(make_df):
    df = make_df([det(conf="l"), det(conf="n"), det(conf="h"), det(conf="n", typ=2), det(conf="h", typ=3), det(conf="h", typ=0)])
    out, funnel = apply_filters(df, exclusions=pd.DataFrame(columns=["lat", "lon", "radius_m", "name", "origin"]))
    assert funnel == {"bbox": 6, "uk_land": 6, "not_static_type": 4, "confidence": 3, "not_persistent": 3}
    assert set(out["confidence"]) == {"n", "h"}


def test_land_filter_drops_sea_points(make_df):
    rows = [det(0, 0)]
    sea = det(0, 0); sea["latitude"], sea["longitude"] = 56.0, 1.0
    rows.append(sea)
    out, funnel = apply_filters(make_df(rows), exclusions=pd.DataFrame(columns=["lat", "lon", "radius_m", "name", "origin"]))
    assert funnel["uk_land"] == 1 and len(out) == 1


def test_persistent_source_exclusion(make_df):
    lat, lon = point(0, 0)
    excl = pd.DataFrame([{"lat": lat, "lon": lon, "radius_m": 1000, "name": "test plant", "origin": "manual"}])
    df = make_df([det(0, 0), det(900, 0), det(1200, 0), det(5000, 0)])
    mask = near_exclusions(df["latitude"].values, df["longitude"].values, excl)
    assert mask.tolist() == [True, True, False, False]
    out, funnel = apply_filters(df, exclusions=excl)
    assert funnel["not_persistent"] == 2 and len(out) == 2
