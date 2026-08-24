"""Command-line entry point.

    python -m pipeline.run backfill [--sources A,B] [--start YYYY-MM-DD] [--end YYYY-MM-DD]
    python -m pipeline.run update
    python -m pipeline.run derive-sources
    python -m pipeline.run sensitivity
    python -m pipeline.run build
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone

import pandas as pd

from . import config

log = logging.getLogger("pipeline")


def _client():
    from .firms_client import FirmsClient
    return FirmsClient()


def cmd_backfill(args):
    from . import ingest
    ingest.backfill(_client(), sources=args.sources.split(",") if args.sources else None,
                    start=date.fromisoformat(args.start) if args.start else None,
                    end=date.fromisoformat(args.end) if args.end else None)


def cmd_update(args):
    from . import ingest
    ingest.update(_client())


def cmd_derive_sources(args):
    from . import ingest, persistent_sources
    from .boundary import points_in_uk
    raw = ingest.load_raw()
    land = raw[points_in_uk(raw["latitude"].values, raw["longitude"].values)]
    sites = persistent_sources.derive(land)
    persistent_sources.write(sites)
    print(sites.head(40).to_string())


def _prepare():
    """Shared load -> filter -> cluster pipeline. Returns (raw, det, events, funnel, end_date)."""
    from . import ingest
    from .cluster import assign_events, summarise_events
    from .filters import apply_filters, load_exclusion_points
    raw = ingest.load_raw()
    if raw.empty:
        raise SystemExit("no raw data - run backfill first")
    det, funnel = apply_filters(raw, load_exclusion_points())
    labels = assign_events(det)
    det = det.copy()
    det["event_id"] = labels
    events = summarise_events(det, labels)
    wl = ingest.read_window_log()
    end = pd.Timestamp(wl["end"].max()).date() if len(wl) else max(raw["acq_date"])
    return raw, det, events, funnel, end


def cmd_sensitivity(args):
    from . import ingest
    from .cluster import assign_events
    from .filters import apply_filters, load_exclusion_points
    raw = ingest.load_raw()
    det, _ = apply_filters(raw, load_exclusion_points())
    years = pd.to_datetime(det["acq_date"]).dt.year
    rows = []
    for eps in config.SENSITIVITY_EPS:
        for gap in config.SENSITIVITY_GAPS:
            labels = assign_events(det, eps_m=eps, max_gap_days=gap)
            first_year = pd.Series(years.values).groupby(labels).min()
            counts = first_year.value_counts().sort_index()
            for y, n in counts.items():
                rows.append({"eps_m": eps, "gap_days": gap, "year": int(y), "events": int(n)})
            log.info("eps=%d gap=%d -> %d events", eps, gap, labels.max() + 1)
    out = pd.DataFrame(rows)
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(config.PROCESSED_DIR / "clustering_sensitivity.csv", index=False)
    print(out.pivot_table(index="year", columns=["eps_m", "gap_days"], values="events").to_string())


def cmd_build(args):
    from . import aggregate, build_site
    raw, det, events, funnel, end = _prepare()
    start = date.fromisoformat(config.HISTORY_START)
    daily = aggregate.daily_table(det, events, start, end)
    monthly = aggregate.monthly_table(daily, events)
    annual = aggregate.annual_table(daily, events)
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    det.to_csv(config.PROCESSED_DIR / "detections_filtered.csv.gz", index=False, compression="gzip")
    events.to_csv(config.PROCESSED_DIR / "events.csv", index=False)
    daily.to_csv(config.PROCESSED_DIR / "daily.csv", index=False)
    monthly.to_csv(config.PROCESSED_DIR / "monthly.csv", index=False)
    annual.to_csv(config.PROCESSED_DIR / "annual.csv", index=False)
    build_site.write_all(raw, det, events, daily, monthly, annual, funnel, end)
    log.info("build complete: %d detections, %d events, %s..%s", len(det), len(events), start, end)
    print(daily.tail(10).to_string())


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill"); b.add_argument("--sources"); b.add_argument("--start"); b.add_argument("--end")
    b.set_defaults(fn=cmd_backfill)
    sub.add_parser("update").set_defaults(fn=cmd_update)
    sub.add_parser("derive-sources").set_defaults(fn=cmd_derive_sources)
    sub.add_parser("sensitivity").set_defaults(fn=cmd_sensitivity)
    sub.add_parser("build").set_defaults(fn=cmd_build)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
