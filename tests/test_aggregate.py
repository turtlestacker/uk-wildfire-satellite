from datetime import date

import numpy as np
import pandas as pd

from pipeline import aggregate
from pipeline.cluster import assign_events, summarise_events
from tests.conftest import det


def _prepared(make_df, rows):
    df = make_df(rows)
    labels = assign_events(df)
    df["event_id"] = labels
    return df, summarise_events(df, labels)


def test_daily_table_zero_fills_and_counts_events(make_df):
    rows = [det(0, 0, "2018-06-24"), det(300, 0, "2018-06-24"), det(0, 0, "2018-06-25"),
            det(8000, 0, "2018-06-25", sat="NOAA20"), det(8000, 0, "2018-06-27")]
    df, events = _prepared(make_df, rows)
    daily = aggregate.daily_table(df, events, date(2018, 6, 22), date(2018, 6, 28))
    assert len(daily) == 7
    by = daily.set_index(daily["date"].dt.strftime("%Y-%m-%d"))
    assert by.loc["2018-06-22", "detections"] == 0 and by.loc["2018-06-22", "active_events"] == 0
    assert by.loc["2018-06-24", "detections"] == 2 and by.loc["2018-06-24", "active_events"] == 1
    assert by.loc["2018-06-25", "detections"] == 2 and by.loc["2018-06-25", "active_events"] == 2
    assert by.loc["2018-06-25", "detections_noaa20"] == 1
    assert by.loc["2018-06-24", "new_events"] == 1 and by.loc["2018-06-25", "new_events"] == 1
    assert by.loc["2018-06-26", "active_events"] == 0  # no detection that day even though the event continues
    assert by.loc["2018-06-27", "active_events"] == 1 and by.loc["2018-06-27", "new_events"] == 0
    # rolling mean uses min_periods=1
    assert by.loc["2018-06-24", "active_events_7d"] == round(1 / 3, 2)


def test_annual_and_monthly(make_df):
    rows = [det(0, 0, "2018-06-24"), det(0, 0, "2019-04-02"), det(6000, 0, "2019-04-02")]
    df, events = _prepared(make_df, rows)
    daily = aggregate.daily_table(df, events, date(2018, 1, 1), date(2019, 12, 31))
    annual = aggregate.annual_table(daily, events).set_index("year")
    assert annual.loc[2018, "new_events"] == 1 and annual.loc[2019, "new_events"] == 2
    assert annual.loc[2019, "peak_day"] == "2019-04-02"
    assert bool(annual.loc[2018, "complete"]) is True
    monthly = aggregate.monthly_table(daily, events)
    m = monthly.set_index(["year", "month"])
    assert m.loc[(2019, 4), "detections"] == 2 and m.loc[(2019, 4), "new_events"] == 2


def test_cumulative_and_same_period(make_df):
    rows = [det(0, 0, "2018-03-01"), det(0, 0, "2019-02-01"), det(6000, 0, "2019-05-01")]
    df, events = _prepared(make_df, rows)
    daily = aggregate.daily_table(df, events, date(2018, 1, 1), date(2019, 6, 30))
    cum = aggregate.cumulative_by_doy(daily)
    assert cum.loc[60, 2018] == 1 and cum.loc[365, 2018] == 1
    assert cum.loc[32, 2019] == 1 and cum.loc[121, 2019] == 2
    stats = aggregate.same_period_stats(daily, date(2019, 6, 30))
    assert stats["ytd"] == 2 and stats["prior_mean"] == 1.0 and stats["rank"] == 1
