import numpy as np

from pipeline.cluster import assign_events, summarise_events
from tests.conftest import det


def n_events(df):
    labels = assign_events(df)
    return len(set(labels)), labels


def test_one_fire_three_nights_is_one_event(make_df):
    rows = []
    for day in ("2018-06-24", "2018-06-25", "2018-06-26"):
        rows += [det(0, 0, day), det(375, 0, day), det(0, 375, day)]
    df = make_df(rows)
    n, labels = n_events(df)
    assert n == 1
    ev = summarise_events(df, labels)
    assert ev.loc[0, "n_days_detected"] == 3
    assert ev.loc[0, "n_detections"] == 9
    assert str(ev.loc[0, "start_date"]) == "2018-06-24" and str(ev.loc[0, "end_date"]) == "2018-06-26"


def test_two_fires_5km_apart_are_two_events(make_df):
    df = make_df([det(0, 0), det(400, 0), det(5000, 0), det(5400, 0)])
    n, labels = n_events(df)
    assert n == 2
    assert labels[0] == labels[1] and labels[2] == labels[3] and labels[0] != labels[2]


def test_gap_of_two_days_links_but_three_does_not(make_df):
    df = make_df([det(0, 0, "2018-06-24"), det(0, 0, "2018-06-26")])
    assert n_events(df)[0] == 1
    df = make_df([det(0, 0, "2018-06-24"), det(0, 0, "2018-06-27")])
    assert n_events(df)[0] == 2


def test_multi_satellite_same_night_is_one_event(make_df):
    df = make_df([det(0, 0, sat="SNPP", time=130), det(200, 100, sat="NOAA20", time=220), det(50, 50, sat="NOAA21", time=310)])
    n, labels = n_events(df)
    assert n == 1
    ev = summarise_events(df, labels)
    assert ev.loc[0, "satellites"] == "NOAA20+NOAA21+SNPP"


def test_single_linkage_chains(make_df):
    # A-B and B-C within eps, A-C beyond: still one event (documented single-linkage behaviour)
    df = make_df([det(0, 0), det(1200, 0), det(2400, 0)])
    assert n_events(df)[0] == 1


def test_labels_are_dense_and_in_first_seen_order(make_df):
    df = make_df([det(0, 0, "2018-06-25"), det(5000, 0, "2018-06-24"), det(10000, 0, "2018-06-26")])
    labels = assign_events(df)
    assert sorted(set(labels)) == [0, 1, 2]
    assert labels[1] == 0  # earliest date gets label 0


def test_custom_parameters(make_df):
    df = make_df([det(0, 0), det(1800, 0)])
    assert len(set(assign_events(df, eps_m=1500))) == 2
    assert len(set(assign_events(df, eps_m=2000))) == 1


def test_empty():
    import pandas as pd
    assert len(assign_events(pd.DataFrame(columns=["latitude", "longitude", "acq_date"]))) == 0
