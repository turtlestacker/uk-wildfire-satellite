"""Paced, retrying client for the NASA FIRMS area CSV API."""
from __future__ import annotations

import io
import json
import logging
import time
from datetime import date, timedelta

import pandas as pd
import requests

from . import config

log = logging.getLogger(__name__)

# Satellite codes differ between SP ("N", "1") and NRT ("N", "N20", "N21") products.
SAT_NORMALISE = {"N": "SNPP", "1": "NOAA20", "N20": "NOAA20", "2": "NOAA21", "N21": "NOAA21"}

RAW_COLUMNS = [
    "latitude", "longitude", "bright_ti4", "scan", "track", "acq_date", "acq_time",
    "satellite", "instrument", "confidence", "version", "bright_ti5", "frp", "daynight", "type",
    "source", "product", "sat",
]


class FirmsError(RuntimeError):
    pass


class FirmsClient:
    def __init__(self, key: str | None = None, session: requests.Session | None = None,
                 pace_s: float = config.FIRMS_REQUEST_INTERVAL_S, status_every: int = 20):
        self.key = key or config.get_map_key()
        self.session = session or requests.Session()
        self.pace_s = pace_s
        self.status_every = status_every
        self._n_requests = 0
        self._last_request = 0.0

    # ------------------------------------------------------------------ helpers
    def _get(self, url: str, timeout: int = 60) -> str:
        delay = 2.0
        for attempt in range(6):
            wait = self.pace_s - (time.time() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.session.get(url, timeout=timeout)
                self._last_request = time.time()
                if r.status_code == 200:
                    return r.text
                if r.status_code in (429, 500, 502, 503, 504):
                    log.warning("FIRMS HTTP %s (attempt %d) - retrying in %.0fs", r.status_code, attempt + 1, delay)
                else:
                    raise FirmsError(f"HTTP {r.status_code}: {r.text[:200]}")
            except requests.RequestException as e:  # network hiccup
                self._last_request = time.time()
                log.warning("FIRMS request error %s (attempt %d) - retrying in %.0fs", e, attempt + 1, delay)
            time.sleep(delay)
            delay = min(delay * 2, 120)
        raise FirmsError("giving up on " + url.replace(self.key, "KEY"))

    def status(self) -> dict:
        txt = self._get(config.FIRMS_KEY_STATUS_URL.format(key=self.key), timeout=30)
        return json.loads(txt)

    def _throttle(self) -> None:
        """Every N requests check the transaction budget and sleep if close to it."""
        self._n_requests += 1
        if self._n_requests % self.status_every:
            return
        try:
            st = self.status()
        except Exception as e:  # status endpoint is best-effort
            log.debug("status check failed: %s", e)
            return
        used = st.get("current_transactions", 0)
        limit = st.get("transaction_limit", config.FIRMS_TRANSACTION_LIMIT)
        while used > limit * 0.9:
            log.info("FIRMS transactions %d/%d - pausing 60s for the window to roll", used, limit)
            time.sleep(60)
            try:
                st = self.status()
                used = st.get("current_transactions", 0)
            except Exception:
                break

    # ------------------------------------------------------------------ public
    def availability(self) -> dict[str, tuple[date, date]]:
        txt = self._get(config.FIRMS_AVAILABILITY_URL.format(key=self.key))
        df = pd.read_csv(io.StringIO(txt))
        return {r.data_id: (pd.Timestamp(r.min_date).date(), pd.Timestamp(r.max_date).date())
                for r in df.itertuples()}

    def fetch_area(self, source: str, start: date, ndays: int = config.FIRMS_MAX_DAYS_PER_REQUEST,
                   bbox: str = config.UK_BBOX_STR) -> pd.DataFrame:
        """Fetch `ndays` (<=5) of detections for `source` starting at `start`, normalised."""
        assert 1 <= ndays <= config.FIRMS_MAX_DAYS_PER_REQUEST
        url = config.FIRMS_AREA_URL.format(key=self.key, source=source, bbox=bbox, days=ndays,
                                           date=start.isoformat())
        self._throttle()
        txt = self._get(url)
        first = txt.lstrip()[:200].lower()
        if not first.startswith("latitude"):
            raise FirmsError(f"unexpected response for {source} {start}: {txt[:200]!r}")
        df = pd.read_csv(io.StringIO(txt))
        return normalise(df, source)


def normalise(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Add source/product/sat columns and make the schema identical across products."""
    df = df.copy()
    if "type" not in df.columns:
        df["type"] = pd.NA
    df["source"] = source
    df["product"] = config.SOURCES[source]["product"]
    df["sat"] = df["satellite"].astype(str).map(SAT_NORMALISE).fillna(df["satellite"].astype(str))
    df["acq_time"] = pd.to_numeric(df["acq_time"], errors="coerce").astype("Int64")
    df["type"] = pd.to_numeric(df["type"], errors="coerce").astype("Int64")
    df["confidence"] = df["confidence"].astype(str).str.lower().str[0]
    return df[RAW_COLUMNS]


def windows(start: date, end: date, size: int = config.FIRMS_MAX_DAYS_PER_REQUEST):
    """Yield (start, ndays) tuples covering [start, end] inclusive."""
    cur = start
    while cur <= end:
        n = min(size, (end - cur).days + 1)
        yield cur, n
        cur += timedelta(days=n)
