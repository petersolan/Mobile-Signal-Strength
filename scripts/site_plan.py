"""Site business case: where each operator should add masts to win back the customers a rival covers
indoors and it does not, and what each site must earn to pay back.

New sites are modelled like the operator's existing cells (build_coverage.py): terrain diffraction over
the LiDAR surface, the operator's clutter slope and offset, the median power and slope of its 800 MHz
cells (the band that carries indoor coverage), a 25 m mast or 4 m above a taller roof. Candidates sit on
a CANDIDATE_M grid near the operator's at-risk customers; sites are chosen greedily by value-weighted
customers gained (scripts/opportunity.py), so each next site adds what the earlier ones did not cover.

Business case: no public data says how many newly covered people switch or stay, so each site gets its
break-even instead: the subscribers it must win or keep for a PAYBACK_YEARS payback, also as a share of
the customers it brings indoor coverage to. Below an operator's market share (~30 %) is plausible.

Both coverage scenarios of gap_analysis.py; in the bias-corrected one, new sites are lowered by the same
operator bias as the existing coverage.

Writes data/derived/site_plan.csv and site_sensitivity.csv.
"""
import os

os.environ.pop("PROJ_LIB", None)   # a system-wide PROJ_LIB (PostGIS) breaks rasterio's bundled PROJ
os.environ.pop("PROJ_DATA", None)

from types import SimpleNamespace

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

from build_coverage import Model, calibrate, load_best
from config import DERIVED, EXTRACTS
from estimate_cells import bin_readings
from gap_analysis import INDOOR, OPERATORS, SCENARIOS, bias_db, indoor, load_addresses
from opportunity import customer_value

CANDIDATE_M = 250      # candidate site spacing
NEAR_M = 750           # candidates within this distance of an at-risk customer
REACH_M = 3000         # a new site's coverage is evaluated this far out
SITES = 5              # sites per operator
LOW_BAND_MHZ = 1000

# Business-case assumptions (base case; ranges in site_sensitivity.csv)
ARPU = 13.24           # GBP per subscriber per month, Ofcom Q4 2025 (all mobile subscribers)
MARGIN = 0.5           # share of ARPU left after the cost of serving a subscriber (assumption)
CAPEX = 150_000        # GBP per new macro site: Ofcom cost model, GBP 105k in 2007/08 prices, inflated
OPEX_SHARE = 0.10      # running cost per year, share of capex (Ofcom cost model)
PAYBACK_YEARS = 5
MARKET_SHARE = 0.30    # each of the three operators, roughly


def break_even(capex=CAPEX, arpu=ARPU):
    """Subscribers a site must win or keep to pay back within PAYBACK_YEARS."""
    return (capex / PAYBACK_YEARS + OPEX_SHARE * capex) / (arpu * 12 * MARGIN)


def new_site(m, x, y, kind):
    h = m.surf.mast_height(x, y)
    return SimpleNamespace(x=x, y=y, h=h, n=kind.n, f=kind.f, a=kind.a), m.surf.horizon(x, y, h)


def predict(m, op, sources, cal, a, uncovered, candidates_near):
    """Candidate sites near an operator's at-risk customers, each with the modelled RSRP it gives the
    addresses it could reach (without the bias correction: scenarios apply it later)."""
    src = OPERATORS[op]
    own = sources[sources.op == src[0]]
    low = own[own.f < LOW_BAND_MHZ]
    kind = SimpleNamespace(a=low.a.median(), n=low.n.median(), f=low.f.median())
    k = cal[src[0]]
    targets = np.flatnonzero(uncovered)                # without indoor 4G in at least one scenario
    tree = cKDTree(a[["x", "y"]].values[targets])
    pts_all = m.points(a.x.values, a.y.values)

    risk = a[candidates_near]
    gx = np.arange(risk.x.min() - NEAR_M, risk.x.max() + NEAR_M, CANDIDATE_M)
    gy = np.arange(risk.y.min() - NEAR_M, risk.y.max() + NEAR_M, CANDIDATE_M)
    cand = np.array(np.meshgrid(gx, gy)).reshape(2, -1).T
    cand = cand[cKDTree(risk[["x", "y"]].values).query(cand, distance_upper_bound=NEAR_M)[0] < np.inf]
    print(f"{op}: {len(cand)} candidate sites, {len(targets):,} addresses without indoor 4G")

    reach = []
    for x, y in cand:
        idx = targets[tree.query_ball_point([x, y], REACH_M)]
        s, hz = new_site(m, x, y, kind)
        pts = {key: v[idx] for key, v in pts_all.items()}
        rsrp = s.a + m.path_db(s, hz, pts) + k["clutter"] * (pts["clutter"] - k["clutter_mean"]) + k["offset"]
        reach.append((idx, rsrp))
    return cand, reach


def plan(op, cand, reach, sc, shift):
    """Greedy site choice for one operator and scenario: each next site adds what the earlier did not."""
    a, cust, w, dfd = sc["a"], sc["customers"], sc["weight"], sc["defend"][op]
    gains = [idx[(rsrp - shift >= INDOOR) & ~sc["covered"][op][idx]] for idx, rsrp in reach]
    taken, rows = np.zeros(len(a), bool), []
    for rank in range(1, SITES + 1):
        score = [w[g[~taken[g]]].sum() for g in gains]
        best = int(np.argmax(score))
        g = gains[best][~taken[gains[best]]]
        taken[g] = True
        rows.append(dict(operator=op, rank=rank, x=cand[best][0], y=cand[best][1],
                         msoa_name=pd.Series(a.msoa_name.values[g]).mode()[0] if len(g) else "",
                         residents=int(a.population.values[g].sum()), customers=round(cust[g].sum()),
                         defend_customers=round(cust[g][dfd[g]].sum()), value_score=round(score[best], 1)))
    return pd.DataFrame(rows)


def scenario(name):
    a = load_addresses(name)
    customers, value, _ = customer_value(a)
    cov = pd.DataFrame({op: indoor(a, src) for op, src in OPERATORS.items()})
    return dict(a=a, customers=customers.fillna(0).values, weight=(customers * value).fillna(0).values,
                covered={op: cov[op].values for op in OPERATORS},
                defend={op: (~cov[op] & cov.drop(columns=op).any(axis=1)).values for op in OPERATORS})


def main():
    scen = {sc: scenario(sc) for sc in SCENARIOS}
    bias = bias_db()
    m = Model(pd.read_parquet(EXTRACTS / "addresses_population.parquet"))
    sources = pd.read_csv(DERIVED / "coverage_cells.csv")
    sources = sources[sources.kind == "4G cell"]
    best_bins = bin_readings(load_best("4G"), keys=("op",))
    cal = calibrate(m, sources, best_bins, [OPERATORS[op][0] for op in OPERATORS])

    sites = []
    for op in OPERATORS:
        # candidates and targets from both scenarios; the bias-corrected one has every modelled gap and more
        uncovered = ~scen["bias-corrected"]["covered"][op]
        near = scen["modelled"]["defend"][op] | scen["bias-corrected"]["defend"][op]
        cand, reach = predict(m, op, sources, cal, scen["modelled"]["a"], uncovered, near)
        for sc in SCENARIOS:
            shift = bias[OPERATORS[op][0]] if sc == "bias-corrected" else 0.0
            sites.append(plan(op, cand, reach, scen[sc], shift).assign(scenario=sc))
    s = pd.concat(sites)

    be = break_even()
    s["break_even_subs"] = round(be)
    s["break_even_pct"] = (100 * be / s.customers).round(1)
    lon, lat = Transformer.from_crs(27700, 4326, always_xy=True).transform(s.x.values, s.y.values)
    s["lon"], s["lat"] = lon.round(5), lat.round(5)
    s.to_csv(DERIVED / "site_plan.csv", index=False)
    print(s[["scenario", "operator", "rank", "msoa_name", "residents", "customers", "defend_customers",
             "break_even_pct"]]
          .to_string(index=False))

    rows = [dict(capex=c, arpu=p, break_even_subs=round(break_even(c, p))) for c in (100_000, 150_000, 300_000)
            for p in (10.0, ARPU, 18.0)]
    sens = pd.DataFrame(rows)
    sens.to_csv(DERIVED / "site_sensitivity.csv", index=False)
    print(f"\nbreak-even subscribers ({PAYBACK_YEARS}-year payback, margin {MARGIN:.0%}):")
    print(sens.pivot(index="capex", columns="arpu", values="break_even_subs").to_string())


if __name__ == "__main__":
    main()
