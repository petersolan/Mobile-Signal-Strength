"""Build a 100 m OSGB grid of Exeter drive-test signal strength from the Ofcom extracts.

Inputs: data/exeter/{4g-lte,5g-nr}-*-exeter.csv (bbox extracts of the Ofcom zips).
Outputs (data/derived/):
  exeter_readings.parquet  long table: tech, year, operator, hour_ref, easting, northing, rsrp, sinr
  exeter_grid_100m.gpkg    one polygon per cell x tech x operator x year (+ year='all')
  exeter_grid_100m.json    compact layers for the web visualisation

Aggregation: readings -> median per (cell, pass) where a pass is one hour_ref, then the
median of pass medians per cell. A van parked with the scanner running is one pass,
so it cannot dominate a cell.
"""
import json

import duckdb
import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import box

from config import DERIVED, EXTRACTS

src = EXTRACTS
out = DERIVED
out.mkdir(exist_ok=True)

CELL = 100
OPS = ["3uk", "ee", "o2", "vf"]
# 5G O2/VF columns are empty (2022, 2024) or near-empty (2023) in Exeter: scanner not on their channels
TECH_OPS = {"4G": OPS, "5G": ["3uk", "ee"]}

con = duckdb.connect()
parts = []
for f in sorted(src.glob("*-exeter.csv")):
    tech = "4G" if f.name.startswith("4g") else "5G"
    rel = f"read_csv('{f.as_posix()}', sample_size=-1, nullstr=['', 'n.a.'])"
    for op in TECH_OPS[tech]:
        parts.append(con.sql(f"""
            SELECT '{tech}' AS tech, year(month_year)::INT AS "year", '{op}' AS "operator",
                   hour_ref::BIGINT AS hour_ref, latitude, longitude,
                   try_cast(rsrp_top1_{op} AS DOUBLE) AS rsrp, try_cast(sinr_top1_{op} AS DOUBLE) AS sinr
            FROM {rel} WHERE rsrp_top1_{op} IS NOT NULL
        """).df())
df = pd.concat(parts, ignore_index=True)
df = df[(df.rsrp > -150) & (df.rsrp < -20)]

tr = Transformer.from_crs(4326, 27700, always_xy=True)
df["easting"], df["northing"] = tr.transform(df.longitude.values, df.latitude.values)
df["cx"] = (df.easting // CELL).astype(int)
df["cy"] = (df.northing // CELL).astype(int)
df.drop(columns=["latitude", "longitude"]).to_parquet(out / "exeter_readings.parquet", index=False)
print(f"readings: {len(df):,}")

keys = ["tech", "operator", "cx", "cy"]
passes = (df.groupby(["tech", "year", "operator", "cx", "cy", "hour_ref"])
            .agg(rsrp=("rsrp", "median"), sinr=("sinr", "median"), n=("rsrp", "size")).reset_index())
by_year = (passes.groupby(keys + ["year"])
             .agg(rsrp=("rsrp", "median"), sinr=("sinr", "median"), n=("n", "sum"), passes=("hour_ref", "nunique"))
             .reset_index())
all_years = (passes.groupby(keys)
               .agg(rsrp=("rsrp", "median"), sinr=("sinr", "median"), n=("n", "sum"), passes=("hour_ref", "nunique"))
               .reset_index().assign(year="all"))
by_year["year"] = by_year.year.astype(str)
cells = pd.concat([by_year, all_years], ignore_index=True)
cells["rsrp"] = cells.rsrp.round(1)
cells["sinr"] = cells.sinr.round(1)
print(f"cell layers: {len(cells):,}")

geom = [box(x * CELL, y * CELL, (x + 1) * CELL, (y + 1) * CELL) for x, y in zip(cells.cx, cells.cy)]
gpd.GeoDataFrame(cells, geometry=geom, crs=27700).to_file(out / "exeter_grid_100m.gpkg", layer="cells", driver="GPKG")

# Compact JSON: layers["4G|ee|2022"] = flat [cx, cy, rsrp*10, passes, n, ...]
cx0, cy0 = int(cells.cx.min()), int(cells.cy.min())
layers = {}
for (tech, op, yr), g in cells.groupby(["tech", "operator", "year"]):
    arr = np.column_stack([g.cx - cx0, g.cy - cy0, (g.rsrp * 10).round().astype(int), g.passes, g.n])
    layers[f"{tech}|{op}|{yr}"] = arr.ravel().tolist()
meta = {
    "cell": CELL, "cx0": cx0, "cy0": cy0,
    "w": int(cells.cx.max()) - cx0 + 1, "h": int(cells.cy.max()) - cy0 + 1,
    "readings": {f"{t}|{o}|{y}": int(n) for (t, o, y), n in df.groupby(["tech", "operator", "year"]).size().items()},
}
(out / "exeter_grid_100m.json").write_text(json.dumps({"meta": meta, "layers": layers}, separators=(",", ":")))
print(f"json: {(out / 'exeter_grid_100m.json').stat().st_size / 1e6:.2f} MB, grid {meta['w']}x{meta['h']}")
