"""Assemble the single-file coverage checker docs/coverage.html from the template, the MapLibre stylesheet,
the OSM basemap and the coverage model outputs of scripts/build_coverage.py.

Data in the page:
  grid       modelled class per 25 m pixel for all four operators in one byte, on a Web Mercator grid:
             code = 27 * Three + 9 * EE + 3 * O2 + Vodafone, each 0 none, 1 outdoor only, 2 indoor; 255 outside
  addresses  populated addresses: lon/lat in 1e-5 degrees (lon delta-coded after sorting), residents,
             MSOA and local authority index, and the same class code
"""
import base64
import json
import os
from pathlib import Path

os.environ.pop("PROJ_LIB", None)    # a system-wide PROJ_LIB (PostGIS) breaks rasterio's bundled PROJ
os.environ.pop("PROJ_DATA", None)

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.warp import Resampling, calculate_default_transform, reproject

here = Path(__file__).resolve().parent
derived = here.parent / "data" / "derived"
OPS = ["3uk", "ee", "o2", "vf"]
INDOOR, OUTDOOR = -95, -105
LADS = {"E07000041": "Exeter", "E07000040": "East Devon", "E07000045": "Teignbridge", "E07000042": "Mid Devon"}


def classes(rsrp):
    return np.select([rsrp >= INDOOR, rsrp >= OUTDOOR], [2, 1], 0).astype(np.uint8)


def grid_layer():
    """All four operators' classes on one Web Mercator grid, plus its corners in lon/lat."""
    codes, meta = None, None
    for i, op in enumerate(OPS):
        with rasterio.open(derived / f"coverage_4g_{op}.tif") as src:
            if meta is None:
                t, w, h = calculate_default_transform(src.crs, "EPSG:3857", src.width, src.height, *src.bounds)
                meta = (t, w, h)
                codes = np.zeros((h, w), np.int32)
                valid = np.zeros((h, w), np.float32)
                reproject(np.ones((src.height, src.width), np.float32), valid, src_transform=src.transform,
                          src_crs=src.crs, dst_transform=t, dst_crs="EPSG:3857", dst_nodata=0,
                          resampling=Resampling.nearest)
            out = np.full((meta[2], meta[1]), -999, np.float32)
            reproject(src.read(1).astype(np.float32), out, src_transform=src.transform, src_crs=src.crs,
                      dst_transform=meta[0], dst_crs="EPSG:3857", dst_nodata=-999, resampling=Resampling.nearest)
            codes += classes(out).astype(np.int32) * 3 ** (3 - i)
    codes = np.where(valid > 0, codes, 255).astype(np.uint8)
    t, w, h = meta
    to_ll = Transformer.from_crs(3857, 4326, always_xy=True)
    west, north = to_ll.transform(t.c, t.f)
    east, south = to_ll.transform(t.c + t.a * w, t.f + t.e * h)
    return dict(w=w, h=h, bounds=[west, south, east, north], b64=base64.b64encode(codes.tobytes()).decode())


def address_layer():
    a = pd.read_parquet(derived / "coverage_addresses.parquet")
    lon, lat = Transformer.from_crs(27700, 4326, always_xy=True).transform(a.x.values, a.y.values)
    a = a.assign(lon=np.round(lon * 1e5).astype(int), lat=np.round(lat * 1e5).astype(int)).sort_values(["lon", "lat"])
    code = sum(classes(a[f"rsrp_{op}"].values).astype(int) * 3 ** (3 - i) for i, op in enumerate(OPS))
    msoas = (a[["msoa21cd", "msoa_name", "lad26cd"]].drop_duplicates("msoa21cd")
               .sort_values("msoa_name").reset_index(drop=True))
    m_idx = {c: i for i, c in enumerate(msoas.msoa21cd)}
    lads = list(LADS)
    return dict(
        lon0=int(a.lon.iloc[0]), dlon=np.diff(a.lon.values, prepend=a.lon.iloc[0]).tolist(),
        lat=a.lat.tolist(), pop=a.population.astype(int).tolist(),
        msoa=[m_idx[c] for c in a.msoa21cd], lad=[lads.index(c) for c in a.lad26cd], code=code.tolist(),
        msoas=[dict(code=r.msoa21cd, name=r.msoa_name, lad=lads.index(r.lad26cd)) for r in msoas.itertuples()],
        lads=[dict(code=c, name=LADS[c]) for c in lads])


def cells_layer():
    c = pd.read_csv(derived / "coverage_cells.csv")
    lon, lat = Transformer.from_crs(27700, 4326, always_xy=True).transform(c.x.values, c.y.values)
    return {op: np.round(np.column_stack([lon[c.op == op], lat[c.op == op]]), 5).tolist() for op in OPS}


def main():
    val = pd.read_csv(derived / "coverage_validation.csv")
    data = dict(grid=grid_layer(), addresses=address_layer(), cells=cells_layer(),
                validation=val.to_dict(orient="records"),
                summary=pd.read_csv(derived / "coverage_summary.csv").to_dict(orient="records"))
    basemap = json.loads((derived / "exeter_map.json").read_text(encoding="utf-8"))["basemap"]
    page = (here / "coverage.template.html").read_text(encoding="utf-8")
    page = page.replace("__MAPLIBRE_CSS__", (here / "maplibre-gl-4.7.1.css").read_text(encoding="utf-8"))
    page = page.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    page = page.replace("__GEO__", json.dumps(basemap, separators=(",", ":")))
    head = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="Modelled indoor and outdoor 4G coverage for Exeter's residents by operator, calibrated to Ofcom drive tests, with area statistics and CSV export.">
<style>:root{color-scheme:light}body{margin:0}[hidden]{display:none!important}img{max-width:100%}</style>
"""
    out = here.parent / "docs" / "coverage.html"
    out.write_text(head + page + "\n</html>\n", encoding="utf-8")
    print(f"{out.name}: {out.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
