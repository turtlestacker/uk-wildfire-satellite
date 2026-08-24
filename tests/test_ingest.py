import pandas as pd

from pipeline import config, ingest
from pipeline.firms_client import RAW_COLUMNS, normalise, windows
from tests.conftest import det


def test_windows_cover_range_in_5_day_chunks():
    from datetime import date
    w = list(windows(date(2024, 1, 1), date(2024, 1, 12)))
    assert w == [(date(2024, 1, 1), 5), (date(2024, 1, 6), 5), (date(2024, 1, 11), 2)]


def test_normalise_handles_missing_type_and_satellite_codes():
    df = pd.DataFrame([{"latitude": 53.5, "longitude": -2.0, "bright_ti4": 330, "scan": .4, "track": .4, "acq_date": "2026-08-20",
                        "acq_time": "0124", "satellite": "N21", "instrument": "VIIRS", "confidence": "nominal", "version": "2.0NRT",
                        "bright_ti5": 290, "frp": 3.2, "daynight": "N"}])
    out = normalise(df, "VIIRS_NOAA21_NRT")
    assert list(out.columns) == RAW_COLUMNS
    assert out.loc[0, "sat"] == "NOAA21" and out.loc[0, "product"] == "NRT"
    assert pd.isna(out.loc[0, "type"]) and out.loc[0, "acq_time"] == 124 and out.loc[0, "confidence"] == "n"


def test_load_raw_prefers_sp_over_nrt_and_dedups(tmp_path, monkeypatch, make_df):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    monkeypatch.setattr(ingest, "WINDOW_LOG", tmp_path / "_windows_fetched.csv")
    sp = make_df([det(0, 0, "2026-04-20", sat="SNPP", product="SP"), det(500, 0, "2026-04-20", sat="SNPP", product="SP")])
    nrt = make_df([det(0, 0, "2026-04-20", sat="SNPP", product="NRT"),           # shadowed by SP for same sat/date
                   det(0, 0, "2026-04-21", sat="SNPP", product="NRT"),           # kept: no SP that day
                   det(0, 0, "2026-04-20", sat="NOAA20", product="NRT"),         # kept: different satellite
                   det(0, 0, "2026-04-20", sat="NOAA20", product="NRT")])        # exact duplicate -> dropped
    sp["source"], nrt["source"] = "VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"
    ingest.upsert_rows("VIIRS_SNPP_SP", sp)
    ingest.upsert_rows("VIIRS_SNPP_NRT", nrt)
    out = ingest.load_raw()
    assert len(out) == 4
    assert sorted(zip(out["sat"], out["product"], out["acq_date"].astype(str))) == [
        ("NOAA20", "NRT", "2026-04-20"), ("SNPP", "NRT", "2026-04-21"), ("SNPP", "SP", "2026-04-20"), ("SNPP", "SP", "2026-04-20")]


def test_upsert_replace_dates_refreshes_trailing_window(tmp_path, monkeypatch, make_df):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    from datetime import date
    old = make_df([det(0, 0, "2026-08-10", product="NRT"), det(0, 0, "2026-08-15", product="NRT")])
    ingest.upsert_rows("VIIRS_SNPP_NRT", old)
    new = make_df([det(900, 0, "2026-08-15", product="NRT"), det(0, 0, "2026-08-16", product="NRT")])
    ingest.upsert_rows("VIIRS_SNPP_NRT", new, replace_dates=(date(2026, 8, 12), date(2026, 8, 20)))
    out = ingest.read_raw_file(ingest.raw_path("VIIRS_SNPP_NRT", 2026))
    assert sorted(out["acq_date"]) == ["2026-08-10", "2026-08-15", "2026-08-16"]
    assert len(out) == 3  # the old 2026-08-15 row was replaced, the 08-10 row outside the window kept
