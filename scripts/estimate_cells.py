"""Estimate where each operator's 4G cells and masts are, from Ofcom drive-test readings.

Every drive-test reading lists each operator's four strongest cells (PCI + EARFCN) and their RSRP. For one
cell, the readings get stronger towards its mast, so fitting a path-loss curve

    RSRP = A - 10 * n * log10(d),   d = distance to the mast (with a 30 m antenna height)

to them gives the mast position (x, y), the power term A and the slope n. Readings are first binned to
20 m squares (median per square), so a slow or parked van does not outweigh the rest of the route.
Cells of one operator whose estimates lie close together are grouped into sites (masts).

Inputs: data/exeter/4g-lte-*-exeter.csv (bbox extracts of the Ofcom zips).
Outputs (data/derived/): exeter_cells.gpkg (one point per cell), exeter_sites.gpkg (one point per site).
"""
import duckdb
import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.optimize import least_squares
from sklearn.cluster import DBSCAN

from config import DERIVED, EXTRACTS

RELEASES = {"4G": ("4g-lte-2022", "4g-lte-2023", "4g-lte-2024"),
            "5G": ("5g-nr-oct22-dec22", "5g-nr-sep23", "5g-nr-2024")}
CHANNEL = ("earfcn", "arfcn", "nr_arfcn")   # the 5G channel column was renamed between releases
OPERATORS = {"3uk": "Three", "ee": "EE", "o2": "O2", "vf": "Vodafone"}
BIN_M = 20            # bin size for readings, m
MIN_BINS = 25         # cells seen in fewer bins are not fitted
MIN_BEST = -95.0      # ... nor cells never stronger than this (dBm): too far away to place
HEIGHT_M = 30.0       # assumed antenna height above the road, m
SITE_EPS_M = 150.0    # cells of one operator closer than this form one site
N_MIN, N_MAX = 2.8, 4.5   # path-loss slope: the urban macro range (2 is free space, which reaches far too far)


def load_readings(tech: str = "4G") -> pd.DataFrame:
    """All four strongest cells of every operator in every reading, one row each. For 5G, the RSRP is
    SS-RSRP and the channel an NR-ARFCN (still called earfcn here)."""
    parts = []
    for release in RELEASES[tech]:
        src = f"read_csv_auto('{(EXTRACTS / f'{release}-exeter.csv').as_posix()}', all_varchar=true)"
        cols = set(duckdb.sql(f"select * from {src} limit 0").columns)
        for op in OPERATORS:
            for k in range(1, 5):
                chan = next((f"{c}_top{k}_{op}" for c in CHANNEL if f"{c}_top{k}_{op}" in cols), None)
                if chan is None or f"rsrp_top{k}_{op}" not in cols:
                    continue
                parts.append(f"select '{release}' yr, '{op}' op, latitude, longitude, hour_ref, "
                             f"pci_top{k}_{op} pci, {chan} earfcn, rsrp_top{k}_{op} rsrp from {src}")
    q = ("select yr, op, latitude::double lat, longitude::double lon, hour_ref, pci::int pci, earfcn::int earfcn, "
         "rsrp::double rsrp from (" + " union all ".join(parts) + ") "
         "where try_cast(rsrp as double) is not null and try_cast(pci as int) is not null "
         "and try_cast(earfcn as int) is not null")
    df = duckdb.sql(q).df()
    x, y = Transformer.from_crs(4326, 27700, always_xy=True).transform(df.lon.values, df.lat.values)
    return df.assign(x=x, y=y)


def fit_cell(b: pd.DataFrame):
    """Least-squares mast position for one cell from its binned readings."""
    top = b.nlargest(max(5, len(b) // 20), "rsrp")
    w = 10 ** (top.rsrp / 10)
    x0, y0 = np.average(top.x, weights=w), np.average(top.y, weights=w)

    def resid(p):
        d = np.sqrt((b.x - p[0]) ** 2 + (b.y - p[1]) ** 2 + HEIGHT_M ** 2)
        return p[2] - 10 * p[3] * np.log10(d) - b.rsrp

    span = 5000
    r = least_squares(resid, [x0, y0, top.rsrp.max() + 60, 3.5], loss="soft_l1", f_scale=6,
                      bounds=([x0 - span, y0 - span, -100, N_MIN], [x0 + span, y0 + span, 100, N_MAX]))
    rmse = float(np.sqrt(np.mean(resid(r.x) ** 2)))
    return r.x, rmse, (x0, y0)


def bin_readings(df: pd.DataFrame, keys=("op", "earfcn", "pci")) -> pd.DataFrame:
    """Median RSRP per BIN_M square (centre coordinates) and group of keys."""
    df = df.assign(bx=(df.x // BIN_M) * BIN_M + BIN_M / 2, by=(df.y // BIN_M) * BIN_M + BIN_M / 2)
    return (df.groupby([*keys, "bx", "by"], as_index=False)
              .agg(rsrp=("rsrp", "median"), n=("rsrp", "size"), passes=("hour_ref", "nunique"))
              .rename(columns={"bx": "x", "by": "y"}))


def fit_cells(bins: pd.DataFrame) -> pd.DataFrame:
    """One fitted mast position and path-loss curve per cell seen often and strongly enough."""
    rows = []
    for (op, earfcn, pci), b in bins.groupby(["op", "earfcn", "pci"]):
        if len(b) < MIN_BINS or b.rsrp.max() < MIN_BEST:
            continue
        (x, y, a, n), rmse, (x0, y0) = fit_cell(b)
        best = b.loc[b.rsrp.idxmax()]
        rows.append(dict(op=op, operator=OPERATORS[op], earfcn=earfcn, pci=pci, x=x, y=y, a=a, n=n,
                         rmse_db=rmse, bins=len(b), best_rsrp=b.rsrp.max(),
                         dist_to_best_m=float(np.hypot(x - best.x, y - best.y)),
                         dist_to_start_m=float(np.hypot(x - x0, y - y0))))
    return pd.DataFrame(rows)


def main():
    DERIVED.mkdir(exist_ok=True)
    cells = fit_cells(bin_readings(load_readings()))

    # sites: cells of one operator, any band, that sit together
    cells["site"] = ""
    for op, g in cells.groupby("op"):
        lab = DBSCAN(eps=SITE_EPS_M, min_samples=1).fit_predict(g[["x", "y"]])
        cells.loc[g.index, "site"] = [f"{op}-{i:03d}" for i in lab]
    sites = (cells.groupby(["site", "operator"], as_index=False)
                  .agg(x=("x", "median"), y=("y", "median"), cells=("pci", "size"),
                       bands=("earfcn", "nunique"), median_rmse_db=("rmse_db", "median")))

    to_gdf = lambda d: gpd.GeoDataFrame(d, geometry=gpd.points_from_xy(d.x, d.y), crs=27700)
    to_gdf(cells).to_file(DERIVED / "exeter_cells.gpkg", layer="cells")
    to_gdf(sites).to_file(DERIVED / "exeter_sites.gpkg", layer="sites")
    print(f"{len(cells)} cells fitted -> {len(sites)} sites")
    print(cells.groupby("operator").agg(cells=("pci", "size"), median_rmse_db=("rmse_db", "median"),
                                        median_n=("n", "median"), median_shift_m=("dist_to_start_m", "median"))
               .round(1).to_string())
    print(sites.groupby("operator").agg(sites=("site", "size"), multi_cell=("cells", lambda c: (c > 1).sum()))
               .to_string())


if __name__ == "__main__":
    main()
