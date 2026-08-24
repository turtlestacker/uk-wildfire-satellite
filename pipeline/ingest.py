"""Backfill and incremental update of raw FIRMS detections.

Storage: data/raw/{SOURCE}/{YEAR}.csv.gz plus a window log so backfills resume.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from . import config
from .firms_client import RAW_COLUMNS, FirmsClient, windows

log = logging.getLogger(__name__)

WINDOW_LOG = config.RAW_DIR / "_windows_fetched.csv"
DEDUP_KEY = ["sat", "acq_date", "acq_time", "latitude", "longitude"]


# ---------------------------------------------------------------- raw storage
def raw_path(source: str, year: int):
    return config.RAW_DIR / source / f"{year}.csv.gz"


def read_raw_file(path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=RAW_COLUMNS)
    df = pd.read_csv(path, dtype={"satellite": str, "version": str, "confidence": str})
    df["type"] = pd.to_numeric(df["type"], errors="coerce").astype("Int64")
    df["acq_time"] = pd.to_numeric(df["acq_time"], errors="coerce").astype("Int64")
    return df[RAW_COLUMNS]


def write_raw_file(source: str, year: int, df: pd.DataFrame) -> None:
    path = raw_path(source, year)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df.drop_duplicates(DEDUP_KEY).sort_values(["acq_date", "acq_time", "latitude", "longitude"])
    df.to_csv(path, index=False, compression="gzip")


def upsert_rows(source: str, new: pd.DataFrame, replace_dates: tuple[date, date] | None = None) -> int:
    """Merge `new` rows into the raw files. If replace_dates is given, existing rows of this
    source within [start, end] are dropped first (used to refresh trailing NRT days)."""
    if new.empty and replace_dates is None:
        return 0
    years = set(pd.to_datetime(new["acq_date"]).dt.year.tolist()) if not new.empty else set()
    if replace_dates:
        years |= set(range(replace_dates[0].year, replace_dates[1].year + 1))
    added = 0
    for year in sorted(years):
        existing = read_raw_file(raw_path(source, year))
        if replace_dates and len(existing):
            d = pd.to_datetime(existing["acq_date"]).dt.date
            existing = existing[(d < replace_dates[0]) | (d > replace_dates[1])]
        part = new[pd.to_datetime(new["acq_date"]).dt.year == year] if not new.empty else new
        merged = pd.concat([existing, part], ignore_index=True)
        before = len(existing)
        merged = merged.drop_duplicates(DEDUP_KEY)
        added += len(merged) - before
        write_raw_file(source, year, merged)
    return added


# ---------------------------------------------------------------- window log
def read_window_log() -> pd.DataFrame:
    if WINDOW_LOG.exists():
        return pd.read_csv(WINDOW_LOG, parse_dates=["start", "end"])
    return pd.DataFrame(columns=["source", "start", "end", "rows", "fetched_at"])


def append_window_log(rows: list[dict]) -> None:
    if not rows:
        return
    log_df = pd.concat([read_window_log(), pd.DataFrame(rows)], ignore_index=True)
    WINDOW_LOG.parent.mkdir(parents=True, exist_ok=True)
    log_df.to_csv(WINDOW_LOG, index=False)


def fetched_windows(source: str) -> set[tuple[date, date]]:
    wl = read_window_log()
    wl = wl[wl["source"] == source]
    return {(pd.Timestamp(s).date(), pd.Timestamp(e).date()) for s, e in zip(wl["start"], wl["end"])}


# ---------------------------------------------------------------- operations
def _fetch_range(client: FirmsClient, source: str, start: date, end: date, skip_done: bool = True) -> int:
    """Fetch [start, end] for `source`, flushing to disk year by year so runs are resumable."""
    done = fetched_windows(source) if skip_done else set()
    buffer: list[pd.DataFrame] = []
    log_rows: list[dict] = []
    current_year = None
    total = 0

    def flush():
        nonlocal buffer, log_rows
        if buffer:
            df = pd.concat(buffer, ignore_index=True)
            if len(df):
                upsert_rows(source, df)
        append_window_log(log_rows)
        buffer, log_rows = [], []

    for wstart, n in windows(start, end):
        wend = wstart + timedelta(days=n - 1)
        if (wstart, wend) in done:
            continue
        if current_year is not None and wstart.year != current_year:
            flush()
            log.info("%s: %d rows through %s", source, total, wstart - timedelta(days=1))
        current_year = wstart.year
        df = client.fetch_area(source, wstart, n)
        total += len(df)
        buffer.append(df)
        log_rows.append({"source": source, "start": wstart, "end": wend, "rows": len(df),
                         "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    flush()
    log.info("%s: fetched %d rows for %s..%s", source, total, start, end)
    return total


def backfill(client: FirmsClient, sources: list[str] | None = None, start: date | None = None,
             end: date | None = None) -> None:
    """Fetch the full available history for each source (resumable)."""
    avail = client.availability()
    for source in sources or list(config.SOURCES):
        if source not in avail:
            log.warning("%s not in availability list - skipping", source)
            continue
        amin, amax = avail[source]
        s = max(amin, start) if start else amin
        e = min(amax, end) if end else amax
        log.info("backfill %s %s..%s", source, s, e)
        _fetch_range(client, source, s, e)


def update(client: FirmsClient) -> None:
    """Daily incremental update: extend SP to its new max date; refresh trailing NRT days."""
    avail = client.availability()
    wl = read_window_log()
    for source, meta in config.SOURCES.items():
        if source not in avail:
            continue
        amin, amax = avail[source]
        if meta["product"] == "SP":
            mine = wl[wl["source"] == source]
            last = pd.Timestamp(mine["end"].max()).date() if len(mine) else None
            s = (last + timedelta(days=1)) if last else amin
            if s <= amax:
                log.info("update %s %s..%s", source, s, amax)
                _fetch_range(client, source, s, amax)
        else:
            e = amax
            s = max(amin, e - timedelta(days=config.NRT_REFRESH_DAYS - 1))
            log.info("refresh %s %s..%s", source, s, e)
            frames = [client.fetch_area(source, ws, n) for ws, n in windows(s, e)]
            new = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=RAW_COLUMNS)
            upsert_rows(source, new, replace_dates=(s, e))
            append_window_log([{"source": source, "start": s, "end": e, "rows": len(new),
                                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}])


def load_raw() -> pd.DataFrame:
    """All raw detections with the SP-over-NRT policy applied and duplicates removed."""
    frames = []
    for source in config.SOURCES:
        d = config.RAW_DIR / source
        if not d.exists():
            continue
        for f in sorted(d.glob("*.csv.gz")):
            frames.append(read_raw_file(f))
    if not frames:
        return pd.DataFrame(columns=RAW_COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    sp = df["product"] == "SP"
    sp_keys = set(zip(df.loc[sp, "sat"], df.loc[sp, "acq_date"]))
    keys = pd.Series(list(zip(df["sat"], df["acq_date"])), index=df.index)
    shadowed = (~sp) & keys.isin(sp_keys)
    df = df[~shadowed]
    df = df.drop_duplicates(DEDUP_KEY).reset_index(drop=True)
    df["acq_date"] = pd.to_datetime(df["acq_date"]).dt.date
    return df
