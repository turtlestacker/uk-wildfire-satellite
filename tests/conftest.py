import numpy as np
import pandas as pd
import pytest
from pyproj import Transformer

from pipeline import config

_inv = Transformer.from_crs(config.CRS_METRIC, "EPSG:4326", always_xy=True)
_fwd = Transformer.from_crs("EPSG:4326", config.CRS_METRIC, always_xy=True)

# Saddleworth Moor, a real wildfire location on open moorland
ORIGIN_LAT, ORIGIN_LON = 53.53, -1.98
OX, OY = _fwd.transform(ORIGIN_LON, ORIGIN_LAT)


def point(dx_m: float, dy_m: float):
    """(lat, lon) offset from the origin by metres east/north."""
    lon, lat = _inv.transform(OX + dx_m, OY + dy_m)
    return lat, lon


def det(dx_m=0, dy_m=0, day="2018-06-24", sat="SNPP", conf="n", frp=5.0, time=130, night="N", typ=None, product="SP"):
    lat, lon = point(dx_m, dy_m)
    return {"latitude": lat, "longitude": lon, "bright_ti4": 330.0, "scan": 0.4, "track": 0.4,
            "acq_date": day, "acq_time": time, "satellite": sat, "instrument": "VIIRS", "confidence": conf,
            "version": "2", "bright_ti5": 290.0, "frp": frp, "daynight": night, "type": typ,
            "source": "TEST", "product": product, "sat": sat}


@pytest.fixture
def make_df():
    def _make(rows):
        df = pd.DataFrame(rows)
        df["type"] = pd.to_numeric(df["type"], errors="coerce").astype("Int64")
        return df
    return _make
