"""UK land boundary from Natural Earth 10m Admin-0 countries, and a point-in-UK test."""
from __future__ import annotations

import io
import json
import logging
import zipfile
from functools import lru_cache
from pathlib import Path

import numpy as np
import requests
import shapefile  # pyshp
from pyproj import Transformer
from shapely.geometry import shape, mapping
from shapely.ops import transform as shp_transform
import shapely

from . import config

log = logging.getLogger(__name__)

NE_URLS = [
    "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries.zip",
    "https://www.naturalearthdata.com/http//www.naturalearthdata.com/download/10m/cultural/ne_10m_admin_0_countries.zip",
]
UK_LAND_GEOJSON = config.STATIC_DIR / "uk_land.geojson"


def build_uk_land(zip_path: Path | None = None, out: Path = UK_LAND_GEOJSON) -> Path:
    """Extract the GBR polygon (Great Britain, Northern Ireland and islands; excludes the Isle of
    Man and Channel Islands, which are separate Natural Earth features) and save as GeoJSON."""
    if zip_path is None:
        cache = config.DATA_DIR / "cache" / "ne_10m_admin_0_countries.zip"
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            for url in NE_URLS:
                try:
                    r = requests.get(url, timeout=120)
                    r.raise_for_status()
                    cache.write_bytes(r.content)
                    break
                except Exception as e:  # try the next mirror
                    log.warning("download failed from %s: %s", url, e)
            else:
                raise RuntimeError("could not download Natural Earth data")
        zip_path = cache
    with zipfile.ZipFile(zip_path) as z:
        shp = io.BytesIO(z.read("ne_10m_admin_0_countries.shp"))
        dbf = io.BytesIO(z.read("ne_10m_admin_0_countries.dbf"))
        shx = io.BytesIO(z.read("ne_10m_admin_0_countries.shx"))
        reader = shapefile.Reader(shp=shp, dbf=dbf, shx=shx)
        fields = [f[0] for f in reader.fields[1:]]
        idx = fields.index("ADM0_A3")
        geom = None
        for sr in reader.iterShapeRecords():
            if sr.record[idx] == "GBR":
                geom = shape(sr.shape.__geo_interface__)
                break
    if geom is None:
        raise RuntimeError("GBR not found in Natural Earth shapefile")
    geom = shapely.make_valid(geom)
    out.parent.mkdir(parents=True, exist_ok=True)
    feature = {"type": "Feature", "properties": {"name": "United Kingdom", "source": "Natural Earth 10m admin-0 v5.1.1"},
               "geometry": mapping(geom)}
    out.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}))
    log.info("wrote %s (%d parts, bounds %s)", out, len(getattr(geom, "geoms", [geom])), geom.bounds)
    return out


@lru_cache(maxsize=1)
def uk_polygon_wgs84():
    if not UK_LAND_GEOJSON.exists():
        build_uk_land()
    gj = json.loads(UK_LAND_GEOJSON.read_text())
    return shape(gj["features"][0]["geometry"])


@lru_cache(maxsize=4)
def uk_polygon_metric(buffer_m: float = config.LAND_BUFFER_M):
    """UK polygon in British National Grid metres, buffered by `buffer_m`."""
    tr = Transformer.from_crs("EPSG:4326", config.CRS_METRIC, always_xy=True)
    poly = shp_transform(tr.transform, uk_polygon_wgs84())
    if buffer_m:
        poly = poly.buffer(buffer_m)
    return poly


def to_metric(lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    tr = Transformer.from_crs("EPSG:4326", config.CRS_METRIC, always_xy=True)
    x, y = tr.transform(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
    return np.asarray(x), np.asarray(y)


def points_in_uk(lat, lon, buffer_m: float = config.LAND_BUFFER_M) -> np.ndarray:
    """Boolean mask: is each (lat, lon) on UK land (within buffer_m of the coastline)?"""
    x, y = to_metric(lat, lon)
    poly = uk_polygon_metric(buffer_m)
    shapely.prepare(poly)
    return shapely.contains_xy(poly, x, y)


if __name__ == "__main__":  # pragma: no cover
    import sys
    logging.basicConfig(level=logging.INFO)
    build_uk_land(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
