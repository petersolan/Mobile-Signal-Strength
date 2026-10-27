"""Assemble the single-file coverage checker docs/coverage.html from the template, the MapLibre stylesheet,
the OSM basemap and the coverage model outputs of scripts/build_coverage.py.

Data in the page, for each network (4G, 5G):
  grids      modelled class per 25 m pixel for all four operators in one byte, on a Web Mercator grid:
             code = 27 * Three + 9 * EE + 3 * O2 + Vodafone, each 0 none, 1 middle, 2 top class
             (4G: outdoor only / indoor; 5G: high / very high confidence); 255 outside
  addresses  residents per 25 m model cell and neighbourhood (not per address): cell centre lon/lat in
             1e-5 degrees (lon delta-coded after sorting), residents, addresses, MSOA and local authority
             index, and the class code per network
"""
import base64
import json
import os
from pathlib import Path

os.environ.pop("PROJ_LIB", None)    # a system-wide PROJ_LIB (PostGIS) breaks rasterio's bundled PROJ
os.environ.pop("PROJ_DATA", None)

import numpy as np
import io

import pandas as pd
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.warp import Resampling, calculate_default_transform, reproject

here = Path(__file__).resolve().parent
derived = here.parent / "data" / "derived"
OPS = ["3uk", "ee", "o2", "vf"]
GRID_M = 25    # the model grid (scripts/build_coverage.py)
THRESHOLDS = {"4g": (-105, -95), "5g": (-110, -100)}   # Ofcom: 4G outdoor / indoor, 5G high / very high
LADS = {"E07000041": "Exeter", "E07000040": "East Devon", "E07000045": "Teignbridge", "E07000042": "Mid Devon"}


def classes(rsrp, tech):
    lo, hi = THRESHOLDS[tech]
    return np.select([rsrp >= hi, rsrp >= lo], [2, 1], 0).astype(np.uint8)


def grid_layer(tech):
    """All four operators' classes on one Web Mercator grid, their signal as 8-bit PNGs, and the corners."""
    codes, meta, pngs = None, None, {}
    for i, op in enumerate(OPS):
        with rasterio.open(derived / f"coverage_{tech}_{op}.tif") as src:
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
            codes += classes(out, tech).astype(np.int32) * 3 ** (3 - i)
            # the signal itself, for the dBm view: 1 = -140 dBm ... 101 = -40 dBm, 0 = outside
            dbm = np.where(valid > 0, np.clip(np.round(out) + 141, 1, 101), 0).astype(np.uint8)
            buf = io.BytesIO()
            Image.fromarray(dbm, mode="L").save(buf, format="PNG", optimize=True)
            pngs[op] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    codes = np.where(valid > 0, codes, 255).astype(np.uint8)
    t, w, h = meta
    to_ll = Transformer.from_crs(3857, 4326, always_xy=True)
    west, north = to_ll.transform(t.c, t.f)
    east, south = to_ll.transform(t.c + t.a * w, t.f + t.e * h)
    return dict(w=w, h=h, bounds=[west, south, east, north], b64=base64.b64encode(codes.tobytes()).decode(),
                dbm=pngs)


def address_layer():
    """Residents per 25 m model cell (and neighbourhood), not per address: no address-level counts in a
    public page. All addresses in a cell share its modelled coverage, so area totals are unchanged."""
    a = pd.read_parquet(derived / "coverage_addresses.parquet")
    a["cx"], a["cy"] = (a.x // GRID_M).astype(int), (a.y // GRID_M).astype(int)
    rsrp = [f"rsrp_{tech}_{op}" for tech in THRESHOLDS for op in OPS]
    a = (a.groupby(["cx", "cy", "msoa21cd", "msoa_name", "lad26cd"], as_index=False)
          .agg(population=("population", "sum"), addresses=("uprn", "size"), **{c: (c, "first") for c in rsrp}))
    x, y = (a.cx + 0.5) * GRID_M, (a.cy + 0.5) * GRID_M
    lon, lat = Transformer.from_crs(27700, 4326, always_xy=True).transform(x.values, y.values)
    a = a.assign(lon=np.round(lon * 1e5).astype(int), lat=np.round(lat * 1e5).astype(int)).sort_values(["lon", "lat"])
    code = {tech: sum(classes(a[f"rsrp_{tech}_{op}"].values, tech).astype(int) * 3 ** (3 - i)
                      for i, op in enumerate(OPS)).tolist() for tech in THRESHOLDS}
    msoas = (a[["msoa21cd", "msoa_name", "lad26cd"]].drop_duplicates("msoa21cd")
               .sort_values("msoa_name").reset_index(drop=True))
    m_idx = {c: i for i, c in enumerate(msoas.msoa21cd)}
    lads = list(LADS)
    return dict(
        lon0=int(a.lon.iloc[0]), dlon=np.diff(a.lon.values, prepend=a.lon.iloc[0]).tolist(),
        lat=a.lat.tolist(), pop=a.population.astype(int).tolist(), n=a.addresses.astype(int).tolist(),
        msoa=[m_idx[c] for c in a.msoa21cd], lad=[lads.index(c) for c in a.lad26cd], code=code,
        msoas=[dict(code=r.msoa21cd, name=r.msoa_name, lad=lads.index(r.lad26cd)) for r in msoas.itertuples()],
        lads=[dict(code=c, name=LADS[c]) for c in lads])


def cells_layer():
    """Estimated 4G cells and modelled 5G sites per operator."""
    c = pd.read_csv(derived / "coverage_cells.csv")
    lon, lat = Transformer.from_crs(27700, 4326, always_xy=True).transform(c.x.values, c.y.values)
    ll = np.round(np.column_stack([lon, lat]), 5)
    tech = np.where(c.kind.str.startswith("5G"), "5g", "4g")
    return {t: {op: ll[(tech == t) & (c.op == op)].tolist() for op in OPS} for t in THRESHOLDS}


def main():
    val = pd.read_csv(derived / "coverage_validation.csv")
    data = dict(grids={t: grid_layer(t) for t in THRESHOLDS}, addresses=address_layer(), cells=cells_layer(),
                validation=val.to_dict(orient="records"),
                summary=[{k: v for k, v in r.items() if pd.notna(v)}   # 4G and 5G rows have different columns
                         for r in pd.read_csv(derived / "coverage_summary.csv").to_dict(orient="records")])
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
<meta name="description" content="Modelled 4G and 5G coverage for Exeter's residents by operator, with terrain from LiDAR, calibrated to Ofcom drive tests, with area statistics and CSV export.">
<style>:root{color-scheme:light}body{margin:0}[hidden]{display:none!important}img{max-width:100%}</style>
"""
    out = here.parent / "docs" / "coverage.html"
    out.write_text(head + page + "\n</html>\n", encoding="utf-8")
    print(f"{out.name}: {out.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
