"""Populated addresses in the study area, for the coverage checker.

Inputs: Census 2021 residents per address (UPRN) and the ONS UPRN Directory, both prepared by the
Census-distribution project (config.CENSUS, read only); House of Commons Library MSOA names.
Outputs (data/exeter/): addresses_population.parquet (uprn, population, x, y in EPSG:27700, local
authority, LSOA and MSOA codes) and msoa_names.csv.
"""
import urllib.request

import duckdb
from pyproj import Transformer

from config import BBOX, CENSUS, EXTRACTS, MSOA_NAMES

south, west, north, east = BBOX
to_bng = Transformer.from_crs(4326, 27700, always_xy=True)
xs, ys = zip(*(to_bng.transform(lon, lat) for lat in (south, north) for lon in (west, east)))
x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)

out = EXTRACTS / "addresses_population.parquet"
duckdb.sql(f"""
copy (
  select p.uprn, p.population, p.geometry.x as x, p.geometry.y as y, o.lad26cd, o.lsoa21cd, o.msoa21cd
  from '{(CENSUS / "uprn_population_points.parquet").as_posix()}' p
  join '{(CENSUS / "onsud_uprn.parquet").as_posix()}' o using (uprn)
  where p.population > 0 and p.geometry.x between {x0} and {x1} and p.geometry.y between {y0} and {y1}
) to '{out.as_posix()}'""")
print(duckdb.sql(f"select count(*) addresses, sum(population) residents from '{out.as_posix()}'").fetchone())

urllib.request.urlretrieve(MSOA_NAMES, EXTRACTS / "msoa_names.csv")
