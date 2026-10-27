"""Model 4G and 5G coverage per operator over the study area, test it on held-out areas, and sample it at
every populated address.

4G. Each cell fitted by estimate_cells.py keeps its estimated mast position; its power A and slope n are
refitted with terrain diffraction (propagation.py) at the cell's own frequency:

    RSRP = A - 10 n log10(d) - J(terrain) - c * (clutter - mean clutter)

clutter is the mean height of buildings and trees (DSM - DTM) within about 150 m, and c (dB per metre)
is fitted per operator. An operator's coverage at a point is its best server, shifted by the operator's
median measured - modelled difference.

5G. The 5G files do not tie most readings to a cell, so 5G is modelled the way it is deployed: a 5G cell
on each of the operator's estimated 4G masts, at the operator's 5G frequency (EE mostly 700 MHz, Three
3.6 GHz), with the 4G cell's power less the extra free-space loss of the higher frequency. EE and Three
are then calibrated to their measured 5G strongest-cell readings like 4G. O2 and Vodafone were not
scanned for 5G in Exeter: their 5G is synthetic, 3.5 GHz on all their estimated masts, with Three's
calibration.

Near the roads, the remaining measured - modelled difference is spread over about SMOOTH_M. Away from
them, a smooth random field (shadow fading: sigma in TECH, about 50 m across, fixed seeds, SHARED of it
common to all operators and networks) stands in for what the LiDAR does not explain; it fades in as the
measurements fade out. Coverage classes use Ofcom's
thresholds: 4G indoor >= -95 dBm, outdoor >= -105; 5G very high confidence >= -100, high >= -110.

Validation: 1 km squares are held out (TEST_SHARE), the model is refitted without their readings and the
squares are predicted, before the random field is added.

Inputs: data/exeter/{4g-lte,5g-nr}-*-exeter.csv, dtm_5m.tif and dsm_5m.tif (prepare_surface.py),
addresses_population.parquet and msoa_names.csv (extract_addresses.py).
Outputs (data/derived/):
  coverage_<tech>_<op>.tif     modelled RSRP, dBm, 25 m OS grid (EPSG:27700)
  coverage_addresses.parquet   every populated address with its modelled RSRP per network and operator
  coverage_cells.csv           modelled 4G cells and 5G sites with their parameters
  coverage_validation.csv      errors on held-out areas
  coverage_summary.csv         residents per coverage class, by network and local authority
"""
import os

# A system-wide PROJ_LIB (PostGIS installs one) overrides the PROJ databases bundled with rasterio and pyproj
os.environ.pop("PROJ_LIB", None)
os.environ.pop("PROJ_DATA", None)

import duckdb
import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from scipy.ndimage import gaussian_filter, uniform_filter
from sklearn.cluster import DBSCAN

import propagation as P
from config import DERIVED, EXTRACTS
from estimate_cells import OPERATORS, RELEASES, SITE_EPS_M, bin_readings, fit_cells, load_readings

GRID_M = 25
SMOOTH_M = 150        # spread of the measured - modelled correction, m
SHRINK = 2.0          # squares' worth of zero correction mixed in, so a lone square counts for less
TEST_SHARE = 0.2
CLUTTER_M = 155       # window for the mean building and tree height around a point, m
SHARED = 0.6          # share of the shadow-fading variance common to all operators and networks
TECH = {"4G": dict(thresholds=(-105.0, -95.0), sigma=4.0, classes=("none", "outdoor", "indoor")),
        "5G": dict(thresholds=(-110.0, -100.0), sigma=6.0, classes=("none", "high", "very high"))}
FIVE_G_MHZ = {"3uk": 3608.0, "ee": 782.6, "o2": 3500.0, "vf": 3500.0}
SYNTHETIC_5G = ("o2", "vf")
LADS = {"E07000041": "Exeter", "E07000040": "East Devon", "E07000045": "Teignbridge", "E07000042": "Mid Devon"}


class Model:
    """Shared geometry: the 25 m grid, the surface, clutter and receiver heights."""

    def __init__(self, addresses):
        x0, x1 = np.floor(addresses.x.min() / 1000) * 1000, np.ceil(addresses.x.max() / 1000) * 1000
        y0, y1 = np.floor(addresses.y.min() / 1000) * 1000, np.ceil(addresses.y.max() / 1000) * 1000
        self.gx, self.gy = np.arange(x0, x1, GRID_M) + GRID_M / 2, np.arange(y1, y0, -GRID_M) - GRID_M / 2
        self.shape = (len(self.gy), len(self.gx))
        self.surf = P.Surface()
        nd = self.surf.dsm - self.surf.dtm
        self.clutter_5m = uniform_filter(nd, size=int(CLUTTER_M / self.surf.cell))
        self.surf.dsm = self.surf.dtm          # diffraction over terrain only; buildings enter as clutter
        X, Y = np.meshgrid(self.gx, self.gy)
        self.grid = self.points(X.ravel(), Y.ravel())

    def points(self, x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        return dict(x=x, y=y, z=self.surf.sample(self.surf.dtm, x, y) + P.RX_M,
                    clutter=self.surf.sample(self.clutter_5m, x, y))

    def at(self, grid, x, y):
        c = np.clip(((np.asarray(x) - self.gx[0]) / GRID_M).round().astype(int), 0, len(self.gx) - 1)
        r = np.clip(((self.gy[0] - np.asarray(y)) / GRID_M).round().astype(int), 0, len(self.gy) - 1)
        return grid[r, c]

    def path_db(self, src, hz, pts):
        """Free-space-like decay plus terrain diffraction from one source to points (no clutter term)."""
        d = np.sqrt((pts["x"] - src.x) ** 2 + (pts["y"] - src.y) ** 2 + (src.h - pts["z"]) ** 2)
        g = P.obstruction(hz, src.x, src.y, src.h, pts["x"], pts["y"], pts["z"])
        with np.errstate(invalid="ignore"):
            return -10 * src.n * np.log10(np.maximum(d, 10.0)) - P.knife_edge_db(g, src.f)

    def best(self, sources, point_sets):
        """Strongest source at each point of each set (before clutter)."""
        out = [np.full(len(p["x"]), -np.inf) for p in point_sets]
        for s in sources.itertuples():
            hz = self.surf.horizon(s.x, s.y, s.h)
            for o, p in zip(out, point_sets):
                np.maximum(o, s.a + self.path_db(s, hz, p), out=o)
        return out

    def correction(self, x, y, resid):
        """Measured - modelled differences spread onto the grid, and how much data stands behind each cell."""
        cols = np.clip(((x - self.gx[0]) / GRID_M).round().astype(int), 0, len(self.gx) - 1)
        rows = np.clip(((self.gy[0] - y) / GRID_M).round().astype(int), 0, len(self.gy) - 1)
        s, n = np.zeros(self.shape), np.zeros(self.shape)
        np.add.at(s, (rows, cols), resid)
        np.add.at(n, (rows, cols), 1)
        sigma = SMOOTH_M / GRID_M
        s, n = gaussian_filter(s, sigma), gaussian_filter(n, sigma)
        k = SHRINK / (2 * np.pi * sigma ** 2)   # a lone square's own peak is 1 / (2 pi sigma^2)
        return s / (n + k), n / (n + k)


def refit_cells(m, cells, bins, tech="4G"):
    """Masts keep their estimated positions and slopes; power is refitted with terrain diffraction."""
    rows = []
    for c in cells.itertuples():
        b = bins[(bins.op == c.op) & (bins.earfcn == c.earfcn) & (bins.pci == c.pci)]
        f = float(P.channel_mhz([c.earfcn], tech)[0])
        h = m.surf.mast_height(c.x, c.y)
        hz = m.surf.horizon(c.x, c.y, h)
        pts = m.points(b.x.values, b.y.values)
        d = np.sqrt((pts["x"] - c.x) ** 2 + (pts["y"] - c.y) ** 2 + (h - pts["z"]) ** 2)
        with np.errstate(invalid="ignore"):
            loss = P.knife_edge_db(P.obstruction(hz, c.x, c.y, h, pts["x"], pts["y"], pts["z"]), f)
        # slope kept from the first fit (power and slope trade off once diffraction is added); power refitted
        a = np.median(b.rsrp.values + loss + 10 * c.n * np.log10(np.maximum(d, 10.0)))
        if np.isfinite(a):
            rows.append(dict(op=c.op, kind="4G cell", earfcn=c.earfcn, pci=c.pci, x=c.x, y=c.y, h=h, f=f,
                             a=a, n=c.n))
    return pd.DataFrame(rows)


def sites_5g(cells4):
    """A 5G cell on each estimated 4G mast, at the operator's 5G frequency."""
    rows = []
    for op, g in cells4.groupby("op"):
        lab = DBSCAN(eps=SITE_EPS_M, min_samples=1).fit_predict(g[["x", "y"]])
        f5 = FIVE_G_MHZ[op]
        for _, s in g.groupby(lab):
            rows.append(dict(op=op, kind="5G synthetic" if op in SYNTHETIC_5G else "5G site", x=s.x.median(),
                             y=s.y.median(), h=s.h.max(), f=f5, n=s.n.median(),
                             a=(s.a - 20 * np.log10(f5 / s.f)).median()))
    return pd.DataFrame(rows)


def load_best(tech):
    """The strongest of each operator's four cells in every reading, cell identified or not."""
    parts = []
    for release in RELEASES[tech]:
        src = f"read_csv_auto('{(EXTRACTS / f'{release}-exeter.csv').as_posix()}', all_varchar=true)"
        cols = set(duckdb.sql(f"select * from {src} limit 0").columns)
        for op in OPERATORS:
            ks = [f"try_cast(rsrp_top{k}_{op} as double)" for k in range(1, 5) if f"rsrp_top{k}_{op}" in cols]
            if ks:
                parts.append(f"select '{release}' yr, '{op}' op, latitude::double lat, longitude::double lon, "
                             f"hour_ref, greatest({', '.join(ks)}) rsrp from {src}")
    df = duckdb.sql(" union all ".join(parts)).df().dropna(subset=["rsrp"])
    x, y = Transformer.from_crs(4326, 27700, always_xy=True).transform(df.lon.values, df.lat.values)
    return df.assign(x=x, y=y)


def calibrate(m, sources, best_bins, ops):
    """Per operator: clutter slope, offset, and the near-road correction from measured best-server bins."""
    cal = {}
    for op in ops:
        b = best_bins[best_bins.op == op]
        src = sources[sources.op == op]
        if b.empty or src.empty:
            continue
        pts = m.points(b.x.values, b.y.values)
        raw = m.best(src, [pts])[0]
        ok = np.isfinite(raw)
        if not ok.all():
            print(f"  {op}: {int((~ok).sum())} of {len(ok)} squares without a finite model value")
        b, raw = b[ok], raw[ok]
        pts = {k: v[ok] for k, v in pts.items()}
        resid = b.rsrp.values - raw
        cm = float(pts["clutter"].mean())
        xc = (pts["clutter"] - cm).astype(float)
        try:
            slope = np.polyfit(xc, resid, 1)[0] if len(xc) >= 30 and xc.std() > 0.01 else 0.0
        except np.linalg.LinAlgError:
            print(f"  {op}: clutter fit failed on {len(xc)} squares (resid finite {np.isfinite(resid).all()})")
            slope = 0.0
        c = float(np.clip(slope, -2.0, 0.0))
        resid = resid - c * (pts["clutter"] - cm)
        off = float(np.median(resid))
        corr, weight = m.correction(b.x.values, b.y.values, resid - off)
        cal[op] = dict(clutter=c, clutter_mean=cm, offset=off, corr=corr, weight=weight)
    return cal


def model_grids(m, sources, cal, ops, extra=()):
    """Corrected grids (no random field) and the uncorrected modelled values at extra point sets."""
    out, at_extra = {}, {}
    for op in ops:
        src = sources[sources.op == op]
        k = cal[op]
        sets = [m.grid, *extra]
        vals = m.best(src, sets)
        adj = [v + k["clutter"] * (p["clutter"] - k["clutter_mean"]) + k["offset"] for v, p in zip(vals, sets)]
        out[op] = adj[0].reshape(m.shape) + k["corr"]
        at_extra[op] = adj[1:]
    return out, at_extra


def fit_cells4(m, readings4):
    bins = bin_readings(readings4)
    return refit_cells(m, fit_cells(bins), bins)


def fit_tech(m, tech, cells4, best):
    """Sources and calibration for one network: 4G cells or 5G sites, calibrated to best-server readings."""
    sources = cells4 if tech == "4G" else sites_5g(cells4)
    best_bins = bin_readings(best, keys=("op",))
    measured = [op for op in OPERATORS if (best_bins.op == op).any()]
    cal = calibrate(m, sources, best_bins, measured)
    if tech == "5G":   # no 5G scans for O2 and Vodafone: Three's 3.5 GHz calibration, no road correction
        for op in SYNTHETIC_5G:
            cal[op] = dict(cal["3uk"], corr=np.zeros(m.shape), weight=np.zeros(m.shape))
    return sources, cal


def classes(rsrp, tech):
    lo, hi = TECH[tech]["thresholds"]
    return np.select([rsrp >= hi, rsrp >= lo], [2, 1], 0)


def validate(m, r4, best, seed=7):
    """Errors on held-out 1 km squares, refitting everything (4G cells included) without them."""
    sq = lambda d: (d.x // 1000).astype(int).astype(str) + "_" + (d.y // 1000).astype(int).astype(str)
    squares = sq(r4).unique()
    test_sq = set(np.random.default_rng(seed).choice(squares, int(len(squares) * TEST_SHARE), replace=False))
    cells4 = fit_cells4(m, r4[~sq(r4).isin(test_sq)])
    rows = []
    for tech in TECH:
        b = best[tech]
        rows += validate_tech(m, tech, cells4, b[~sq(b).isin(test_sq)], b[sq(b).isin(test_sq)])
    return pd.DataFrame(rows), len(test_sq), len(squares)


def validate_tech(m, tech, cells4, train, test):
    sources, cal = fit_tech(m, tech, cells4, train)
    held = bin_readings(test, keys=("op",))
    rows = []
    for op, h in held.groupby("op"):
        if op not in cal or op in (SYNTHETIC_5G if tech == "5G" else ()):
            continue
        k = cal[op]
        pts = m.points(h.x.values, h.y.values)
        raw = m.best(sources[sources.op == op], [pts])[0] + k["clutter"] * (pts["clutter"] - k["clutter_mean"]) + k["offset"]
        for name, pred in (("model", raw), ("model + correction", raw + m.at(k["corr"], h.x.values, h.y.values))):
            e = pred - h.rsrp.values
            rows.append(dict(network=tech, operator=OPERATORS[op], prediction=name, squares_20m=len(h),
                             bias_db=e.mean(), mae_db=np.abs(e).mean(), rmse_db=np.sqrt((e ** 2).mean()),
                             class_agreement_pct=100 * (classes(pred, tech) == classes(h.rsrp.values, tech)).mean()))
    return rows


def main():
    addresses = pd.read_parquet(EXTRACTS / "addresses_population.parquet")
    m = Model(addresses)
    r4 = load_readings("4G")
    best = {"4G": load_best("4G"), "5G": load_best("5G")}

    val, n_test, n_sq = validate(m, r4, best)
    val = val.round(2)
    val.to_csv(DERIVED / "coverage_validation.csv", index=False)
    print(f"Held out {n_test} of {n_sq} 1 km squares:\n{val.to_string(index=False)}\n")

    cells4 = fit_cells4(m, r4)
    transform = from_origin(m.gx[0] - GRID_M / 2, m.gy[0] + GRID_M / 2, GRID_M, GRID_M)

    def field(seed):
        f = gaussian_filter(np.random.default_rng(seed).standard_normal(m.shape), 2.0)
        return f / f.std()

    shared = field(0)     # the same trees and buildings get in every network's way
    all_sources, rows = [], []
    for t, (tech, spec) in enumerate(TECH.items()):
        sources, cal = fit_tech(m, tech, cells4, best[tech])
        all_sources.append(sources)
        grids, _ = model_grids(m, sources, cal, list(OPERATORS))
        for i, op in enumerate(OPERATORS):
            noise = spec["sigma"] * (np.sqrt(SHARED) * shared + np.sqrt(1 - SHARED) * field(100 * (t + 1) + i))
            rsrp = np.clip(grids[op] + noise * (1 - cal[op]["weight"]), -140, -40)
            with rasterio.open(DERIVED / f"coverage_{tech.lower()}_{op}.tif", "w", driver="GTiff", height=m.shape[0],
                               width=m.shape[1], count=1, dtype="int16", crs="EPSG:27700", transform=transform,
                               compress="deflate") as dst:
                dst.write(np.round(rsrp).astype("int16"), 1)
            addresses[f"rsrp_{tech.lower()}_{op}"] = np.round(m.at(rsrp, addresses.x.values, addresses.y.values)).astype("int16")
            k = cal[op]
            print(f"{tech} {OPERATORS[op]}: {int((sources.op == op).sum())} sources, clutter {k['clutter']:.2f} dB/m, "
                  f"offset {k['offset']:.1f} dB")
    pd.concat(all_sources).round(2).to_csv(DERIVED / "coverage_cells.csv", index=False)

    names = pd.read_csv(EXTRACTS / "msoa_names.csv", encoding="utf-8-sig")[["msoa21cd", "msoa21hclnm"]]
    addresses = addresses.merge(names, on="msoa21cd", how="left").rename(columns={"msoa21hclnm": "msoa_name"})
    addresses.to_parquet(DERIVED / "coverage_addresses.parquet", index=False)

    for tech, spec in TECH.items():
        for lad, a in [("All", addresses), *((LADS[k], g) for k, g in addresses.groupby("lad26cd"))]:
            row = dict(network=tech, area=lad, residents=int(a.population.sum()))
            top = []
            for op, name in OPERATORS.items():
                c = classes(a[f"rsrp_{tech.lower()}_{op}"].values.astype(float), tech)
                top.append(c == 2)
                for k, label in enumerate(spec["classes"]):
                    row[f"{name} {label} %"] = round(100 * a.population[c == k].sum() / a.population.sum(), 1)
            top = np.column_stack(top)
            row[f"{spec['classes'][2]} from all 4 %"] = round(100 * a.population[top.all(1)].sum() / a.population.sum(), 1)
            row[f"{spec['classes'][2]} from none %"] = round(100 * a.population[~top.any(1)].sum() / a.population.sum(), 1)
            rows.append(row)
    summary = pd.DataFrame(rows)
    summary.to_csv(DERIVED / "coverage_summary.csv", index=False)
    print(summary[summary.area.isin(["Exeter"])].T.to_string())


if __name__ == "__main__":
    main()
