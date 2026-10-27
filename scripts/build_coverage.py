"""Model 4G coverage per operator over the study area, test it on held-out areas, and sample it at every
populated address.

Model: each cell fitted by estimate_cells.py predicts RSRP = A - 10 n log10(d) around its estimated mast;
an operator's coverage at a point is its best server, the strongest of its cells there, shifted by the
operator's median measured - modelled difference (the strongest of many noisy curves runs high). Where the drive
tests measured something else, the difference (measured - modelled, per 20 m square) is spread around
the measurements with a Gaussian of SMOOTH_M and fades to nothing away from them, so roads match the
measurements and everywhere else follows the model.

Validation: 1 km OS squares are held out at random (TEST_SHARE of them); cells are refitted and the
correction rebuilt without their readings, and the held-out squares are predicted. Held-out areas rather
than held-out drive passes, because the vans drive the same roads again and again.

Coverage classes are Ofcom's 4G thresholds for RSRP: indoor >= -95 dBm, outdoor >= -105 dBm.
The model is an estimate from open data, not operator coverage: no terrain, clutter or antenna patterns.

Inputs: data/exeter/4g-lte-*-exeter.csv, data/exeter/addresses_population.parquet (Census 2021 residents
per address, from the Census-distribution project), data/exeter/msoa_names.csv (House of Commons Library).
Outputs (data/derived/):
  coverage_4g_<op>.tif            modelled RSRP, dBm, 25 m OS grid (EPSG:27700)
  coverage_addresses.parquet      every populated address with its modelled RSRP per operator
  coverage_validation.csv         errors on held-out areas, model only and with the correction
  coverage_summary.csv            residents with indoor / outdoor / no coverage, by local authority
"""
import os

# A system-wide PROJ_LIB (PostGIS installs one) overrides the PROJ databases bundled with rasterio and pyproj
os.environ.pop("PROJ_LIB", None)
os.environ.pop("PROJ_DATA", None)

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin
from scipy.ndimage import gaussian_filter

from config import DERIVED, EXTRACTS
from estimate_cells import HEIGHT_M, OPERATORS, bin_readings, fit_cells, load_readings

GRID_M = 25
SMOOTH_M = 150        # spread of the measured - modelled correction, m
SHRINK = 2.0          # squares' worth of zero correction mixed in, so a lone square counts for less
TEST_SHARE = 0.2
INDOOR, OUTDOOR = -95.0, -105.0
LADS = {"E07000041": "Exeter", "E07000040": "East Devon", "E07000045": "Teignbridge", "E07000042": "Mid Devon"}


def grid_axes(addresses):
    """25 m cell centres covering the addresses and drive tests (west->east, north->south)."""
    x0, x1 = np.floor(addresses.x.min() / 1000) * 1000, np.ceil(addresses.x.max() / 1000) * 1000
    y0, y1 = np.floor(addresses.y.min() / 1000) * 1000, np.ceil(addresses.y.max() / 1000) * 1000
    return np.arange(x0, x1, GRID_M) + GRID_M / 2, np.arange(y1, y0, -GRID_M) - GRID_M / 2


def best_server(cells, x, y):
    """Strongest modelled RSRP of an operator's cells at points x, y (any shape)."""
    out = np.full(np.shape(x), -np.inf)
    for c in cells.itertuples():
        d = np.sqrt((x - c.x) ** 2 + (y - c.y) ** 2 + HEIGHT_M ** 2)
        np.maximum(out, c.a - 10 * c.n * np.log10(d), out=out)
    return out


def correction(points, gx, gy):
    """Measured - modelled differences at points (x, y, resid) spread onto the grid."""
    cols = np.clip(((points.x - gx[0]) / GRID_M).round().astype(int), 0, len(gx) - 1)
    rows = np.clip(((gy[0] - points.y) / GRID_M).round().astype(int), 0, len(gy) - 1)
    s, n = np.zeros((len(gy), len(gx))), np.zeros((len(gy), len(gx)))
    np.add.at(s, (rows, cols), points.resid)
    np.add.at(n, (rows, cols), 1)
    sigma = SMOOTH_M / GRID_M
    s, n = gaussian_filter(s, sigma), gaussian_filter(n, sigma)
    return s / (n + SHRINK / (2 * np.pi * sigma ** 2))   # a lone square's own peak is 1 / (2 pi sigma^2)


def at(grid, gx, gy, x, y):
    """Grid value at points (nearest cell)."""
    c = np.clip(((x - gx[0]) / GRID_M).round().astype(int), 0, len(gx) - 1)
    r = np.clip(((gy[0] - y) / GRID_M).round().astype(int), 0, len(gy) - 1)
    return grid[r, c]


def classes(rsrp):
    return np.select([rsrp >= INDOOR, rsrp >= OUTDOOR], ["indoor", "outdoor"], "none")


def model(readings, gx, gy):
    """Fitted cells and the corrected best-server grid per operator from these readings."""
    cells = fit_cells(bin_readings(readings))
    best = bin_readings(best_readings(readings), keys=("op",))
    X, Y = np.meshgrid(gx, gy)
    grids = {}
    for op in OPERATORS:
        c, b = cells[cells.op == op], best[best.op == op]
        # the best of many noisy cell curves runs high, so shift each operator by its median difference
        b = b.assign(resid=b.rsrp - best_server(c, b.x.values, b.y.values))
        offset = b.resid.median()
        grids[op] = (best_server(c, X, Y) + offset, correction(b.assign(resid=b.resid - offset), gx, gy))
    return cells, grids


def best_readings(df):
    """The strongest of each operator's four cells in every reading."""
    return df.groupby(["op", "yr", "hour_ref", "x", "y"], as_index=False).agg(rsrp=("rsrp", "max"))


def validate(df, gx, gy, seed=7):
    sq = (df.x // 1000).astype(int).astype(str) + "_" + (df.y // 1000).astype(int).astype(str)
    squares = sq.unique()
    test_sq = set(np.random.default_rng(seed).choice(squares, int(len(squares) * TEST_SHARE), replace=False))
    test = sq.isin(test_sq)
    _, grids = model(df[~test], gx, gy)
    held = bin_readings(best_readings(df[test]), keys=("op",))
    rows = []
    for op, h in held.groupby("op"):
        base, corr = grids[op]
        m = at(base, gx, gy, h.x.values, h.y.values)
        for name, pred in (("model", m), ("model + correction", m + at(corr, gx, gy, h.x.values, h.y.values))):
            e = pred - h.rsrp.values
            rows.append(dict(operator=OPERATORS[op], prediction=name, squares_20m=len(h), bias_db=e.mean(),
                             mae_db=np.abs(e).mean(), rmse_db=np.sqrt((e ** 2).mean()),
                             class_agreement_pct=100 * (classes(pred) == classes(h.rsrp.values)).mean()))
    return pd.DataFrame(rows).round(2), len(test_sq), len(squares)


def main():
    addresses = pd.read_parquet(EXTRACTS / "addresses_population.parquet")
    gx, gy = grid_axes(addresses)
    df = load_readings()

    val, n_test, n_sq = validate(df, gx, gy)
    val.to_csv(DERIVED / "coverage_validation.csv", index=False)
    print(f"Held out {n_test} of {n_sq} 1 km squares:\n{val.to_string(index=False)}\n")

    cells, grids = model(df, gx, gy)
    transform = from_origin(gx[0] - GRID_M / 2, gy[0] + GRID_M / 2, GRID_M, GRID_M)
    for op, (base, corr) in grids.items():
        rsrp = np.clip(base + corr, -140, -40)
        with rasterio.open(DERIVED / f"coverage_4g_{op}.tif", "w", driver="GTiff", height=rsrp.shape[0],
                           width=rsrp.shape[1], count=1, dtype="int16", crs="EPSG:27700", transform=transform,
                           compress="deflate") as dst:
            dst.write(np.round(rsrp).astype("int16"), 1)
        addresses[f"rsrp_{op}"] = np.round(at(rsrp, gx, gy, addresses.x.values, addresses.y.values)).astype("int16")
    cells.to_csv(DERIVED / "coverage_cells.csv", index=False)

    names = pd.read_csv(EXTRACTS / "msoa_names.csv", encoding="utf-8-sig")[["msoa21cd", "msoa21hclnm"]]
    addresses = addresses.merge(names, on="msoa21cd", how="left").rename(columns={"msoa21hclnm": "msoa_name"})
    addresses.to_parquet(DERIVED / "coverage_addresses.parquet", index=False)

    rows = []
    for lad, a in [("All", addresses), *((LADS[k], g) for k, g in addresses.groupby("lad26cd"))]:
        row = dict(area=lad, residents=int(a.population.sum()))
        for op, name in OPERATORS.items():
            c = classes(a[f"rsrp_{op}"].values.astype(float))
            for k in ("indoor", "outdoor", "none"):
                row[f"{name} {k} %"] = round(100 * a.population[c == k].sum() / a.population.sum(), 1)
        ind = np.column_stack([a[f"rsrp_{op}"] >= INDOOR for op in OPERATORS])
        row["indoor from all 4 %"] = round(100 * a.population[ind.all(1)].sum() / a.population.sum(), 1)
        row["indoor from none %"] = round(100 * a.population[~ind.any(1)].sum() / a.population.sum(), 1)
        rows.append(row)
    summary = pd.DataFrame(rows)
    summary.to_csv(DERIVED / "coverage_summary.csv", index=False)
    print(summary.set_index("area").T.to_string())


if __name__ == "__main__":
    main()
