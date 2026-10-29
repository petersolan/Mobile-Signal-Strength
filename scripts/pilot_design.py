"""Pilot design: how much driving settles whether EE's model is right or a few dB optimistic.

The business case gives EE two answers: as modelled its sites do not pay, bias-corrected they do. The
bias-corrected scenario rests on the model's mean error on held-out areas (about +6 dB for EE). A pilot drive
in the three target areas measures that error where it matters. This script asks how big the drive must be
for the call to be right, by resampling the held-out errors themselves (scripts/coverage_match.held_out):
readings close together share their error, so the number of different 1 km squares driven matters far
more than the number of readings.

For each design (1 km squares driven x 20 m road squares measured in each) it draws 4,000 pilots from the
held-out EE errors, clusters and all, and reports the spread of the measured mean error and how often the
pilot makes the right call with the decision rule "build if the mean error is above half the held-out bias",
both when the model is in truth unbiased and when it is as optimistic as the held-out areas say.

Writes data/derived/pilot_design.csv.
"""
import os

os.environ.pop("PROJ_LIB", None)   # a system-wide PROJ_LIB (PostGIS) breaks rasterio's bundled PROJ
os.environ.pop("PROJ_DATA", None)

import numpy as np
import pandas as pd

from config import DERIVED
from coverage_match import held_out

OPERATOR = "ee"
KM_SQUARES = (1, 2, 3, 5, 8, 12)
PER_SQUARE = (10, 30, 100)
DRAWS = 4000


def icc(e: pd.Series, cluster: pd.Series) -> float:
    """Share of the error's variance that lies between 1 km squares (one-way ANOVA estimator)."""
    g = e.groupby(cluster)
    k, n = g.ngroups, len(e)
    n0 = (n - (g.size() ** 2).sum() / n) / (k - 1)
    msb = (g.size() * (g.mean() - e.mean()) ** 2).sum() / (k - 1)
    msw = ((e - g.transform("mean")) ** 2).sum() / (n - k)
    return max(0.0, (msb - msw) / (msb + (n0 - 1) * msw))


def simulate(e: pd.Series, cluster: pd.Series, m: int, k: int, rng) -> np.ndarray:
    """Mean error of DRAWS pilots, each m 1 km squares (resampled) with k road squares in each."""
    groups = [g.to_numpy() for _, g in e.groupby(cluster)]
    out = np.empty(DRAWS)
    for i in range(DRAWS):
        picks = rng.integers(len(groups), size=m)
        out[i] = np.mean([rng.choice(groups[j], size=k, replace=True).mean() for j in picks])
    return out


def main():
    h = held_out()
    h = h[h.op == OPERATOR]
    e = pd.Series(h.modelled.to_numpy() - h.rsrp.to_numpy())   # modelled - measured, dB
    cluster = pd.Series(h.km_square.to_numpy())
    bias = e.mean()
    rho = icc(e, cluster)
    print(f"EE held-out: {len(e)} road squares in {cluster.nunique()} 1 km squares, mean error {bias:+.1f} dB, "
          f"sd {e.std():.1f} dB, share of variance between 1 km squares {rho:.2f}")

    rng = np.random.default_rng(7)
    threshold = bias / 2
    rows = []
    for m in KM_SQUARES:
        for k in PER_SQUARE:
            optimistic = simulate(e, cluster, m, k, rng)           # the world the held-out areas show
            unbiased = optimistic - bias                            # the same errors, centred on zero
            rows.append(dict(km_squares=m, road_squares_each=k, road_squares=m * k,
                             ci95_db=round(1.96 * optimistic.std(), 1),
                             right_if_optimistic_pct=round(100 * (optimistic > threshold).mean(), 1),
                             right_if_unbiased_pct=round(100 * (unbiased <= threshold).mean(), 1)))
    t = pd.DataFrame(rows)
    t["right_call_pct"] = t[["right_if_optimistic_pct", "right_if_unbiased_pct"]].min(axis=1)
    t.attrs = {}
    t.to_csv(DERIVED / "pilot_design.csv", index=False)
    print(f"decision rule: build if the pilot's mean error is above {threshold:.1f} dB")
    print(t.to_string(index=False))


if __name__ == "__main__":
    main()
