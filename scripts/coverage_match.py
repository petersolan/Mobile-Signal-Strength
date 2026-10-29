"""Does the modelled coverage match the drive tests? On the held-out 1 km squares of build_coverage.py's
validation (same split, same refit without their readings), the share of 20 m road squares in each 4G
coverage class, measured and modelled, per operator, and how often the model puts a square in a better
or worse class than the measurement.

Writes data/derived/coverage_match.csv.
"""
import os

os.environ.pop("PROJ_LIB", None)   # a system-wide PROJ_LIB (PostGIS) breaks rasterio's bundled PROJ
os.environ.pop("PROJ_DATA", None)

import numpy as np
import pandas as pd

import build_coverage as B
from config import DERIVED, EXTRACTS
from estimate_cells import OPERATORS, bin_readings, load_readings

CLASSES = ["none", "outdoor_only", "indoor"]


def square(d):
    """The 1 km OS grid square of each reading, as build_coverage.validate splits them."""
    return (d.x // 1000).astype(int).astype(str) + "_" + (d.y // 1000).astype(int).astype(str)


def main():
    m = B.Model(pd.read_parquet(EXTRACTS / "addresses_population.parquet"))
    r4, best = load_readings("4G"), B.load_best("4G")
    squares = square(r4).unique()
    test = set(np.random.default_rng(7).choice(squares, int(len(squares) * B.TEST_SHARE), replace=False))
    cells4 = B.fit_cells4(m, r4[~square(r4).isin(test)])
    sources, cal = B.fit_tech(m, "4G", cells4, best[~square(best).isin(test)])
    held = bin_readings(best[square(best).isin(test)], keys=("op",))

    rows = []
    for op, h in held.groupby("op"):
        k = cal[op]
        pts = m.points(h.x.values, h.y.values)
        pred = (m.best(sources[sources.op == op], [pts])[0] + k["clutter"] * (pts["clutter"] - k["clutter_mean"])
                + k["offset"] + m.at(k["corr"], h.x.values, h.y.values))
        measured, modelled = B.classes(h.rsrp.values, "4G"), B.classes(pred, "4G")
        row = dict(operator=OPERATORS[op], squares_20m=len(h))
        for i, c in enumerate(CLASSES):
            row[f"measured_{c}_pct"] = 100 * (measured == i).mean()
            row[f"modelled_{c}_pct"] = 100 * (modelled == i).mean()
        row.update(same_class_pct=100 * (measured == modelled).mean(),
                   model_better_pct=100 * (modelled > measured).mean(),
                   model_worse_pct=100 * (modelled < measured).mean())
        rows.append(row)
    t = pd.DataFrame(rows).round(1)
    t.to_csv(DERIVED / "coverage_match.csv", index=False)
    print(t.to_string(index=False))


if __name__ == "__main__":
    main()
