"""Competitive coverage gaps: for every neighbourhood (MSOA) and operator, the share of residents with
indoor 4G, the gap to the best competitor, and how many residents each operator could win or lose on
coverage alone.

Operators are the three UK networks after the Vodafone-Three merger: EE, VMO2 and VodafoneThree, whose
coverage at an address is the better of the Vodafone and Three signals (the networks are being combined).
Vodafone on its own is kept too, to show what the merger adds. Indoor 4G only: the 5G model is measured for
EE and Three alone, so 5G is left out of the comparison.

Per operator and MSOA:
  indoor_pct     residents with indoor 4G (>= -95 dBm, Ofcom)
  gap_pp         indoor_pct minus the best competitor's, in percentage points
  at_risk        residents without indoor 4G from this operator but with it from a competitor: the
                 customers it would lose on coverage
  advantage      residents only this operator covers indoors
Per MSOA: not_spot, residents no operator covers indoors.

Two scenarios: "modelled", and "bias-corrected", where each operator's signal is lowered by the model's
mean error on held-out drive-test areas (coverage_validation.csv, about 4-6 dB): the model is optimistic
where it has not seen measurements, so the modelled gaps are lower bounds.

Writes data/derived/gap_msoa.csv (one row per scenario, MSOA and operator) and gap_summary.csv.
"""
import pandas as pd

from config import DERIVED

INDOOR = -95   # dBm, Ofcom's indoor 4G threshold
OPERATORS = {"EE": ["ee"], "VMO2": ["o2"], "VodafoneThree": ["vf", "3uk"]}
SOLO = {"Vodafone": ["vf"]}   # before the merger, for comparison only
SCENARIOS = ("modelled", "bias-corrected")
VALIDATION_NAMES = {"Three": "3uk", "EE": "ee", "O2": "o2", "Vodafone": "vf"}


def bias_db():
    """Each operator's mean 4G error (modelled - measured, dB) on held-out areas, road correction included."""
    v = pd.read_csv(DERIVED / "coverage_validation.csv")
    v = v[(v.network == "4G") & (v.prediction == "model + correction")]
    return {VALIDATION_NAMES[o]: b for o, b in zip(v.operator, v.bias_db)}


def load_addresses(scenario="modelled"):
    """Populated addresses with their modelled 4G and 5G RSRP; 4G lowered by the bias if so asked."""
    a = pd.read_parquet(DERIVED / "coverage_addresses.parquet")
    a = a[a.population > 0].reset_index(drop=True)
    if scenario == "bias-corrected":
        for op, b in bias_db().items():
            a[f"rsrp_4g_{op}"] = a[f"rsrp_4g_{op}"] - b
    return a


def indoor(a, sources):
    return a[[f"rsrp_4g_{s}" for s in sources]].max(axis=1) >= INDOOR


def operator_table(a, ops):
    """Per MSOA and operator: indoor share, gap to the best competitor, residents at risk / advantaged."""
    cov = pd.DataFrame({op: indoor(a, src) for op, src in ops.items()})
    rows = []
    for op in ops:
        rivals = cov[[o for o in OPERATORS if o != op and o in cov]].any(axis=1)
        rows.append(pd.DataFrame({
            "msoa21cd": a.msoa21cd, "msoa_name": a.msoa_name, "operator": op, "residents": a.population,
            "indoor": a.population * cov[op], "at_risk": a.population * (~cov[op] & rivals),
            "advantage": a.population * (cov[op] & ~rivals)}))
    t = pd.concat(rows).groupby(["msoa21cd", "msoa_name", "operator"], as_index=False).sum()
    t["indoor_pct"] = 100 * t.indoor / t.residents
    best_rival = t.groupby("msoa21cd").apply(
        lambda g: pd.Series({op: g.loc[g.operator != op, "indoor_pct"].max() for op in g.operator}),
        include_groups=False).stack().rename("best_rival_pct")
    t = t.join(best_rival, on=["msoa21cd", "operator"])
    t["gap_pp"] = t.indoor_pct - t.best_rival_pct
    return t


def scenario_table(a):
    t = operator_table(a, OPERATORS)
    solo = operator_table(a, {"Vodafone": SOLO["Vodafone"], "EE": ["ee"], "VMO2": ["o2"]})
    t = pd.concat([t, solo[solo.operator == "Vodafone"]])
    any_op = pd.concat([indoor(a, src) for src in OPERATORS.values()], axis=1).any(axis=1)
    not_spot = (a.population * ~any_op).groupby(a.msoa21cd).sum().rename("not_spot")
    return t.join(not_spot, on="msoa21cd")


def main():
    ops = [*OPERATORS, *SOLO]
    t = pd.concat([scenario_table(load_addresses(sc)).assign(scenario=sc) for sc in SCENARIOS])
    t = t.round({"indoor_pct": 1, "best_rival_pct": 1, "gap_pp": 1}).sort_values(["scenario", "operator", "gap_pp"])
    t.to_csv(DERIVED / "gap_msoa.csv", index=False)

    s = t.groupby(["scenario", "operator"])[["residents", "indoor", "at_risk", "advantage"]].sum()
    s["indoor_pct"] = (100 * s.indoor / s.residents).round(1)
    not_spot = t.drop_duplicates(["scenario", "msoa21cd"]).groupby("scenario").not_spot.sum()
    s["not_spot"] = s.index.get_level_values("scenario").map(not_spot)
    s = s.reindex(pd.MultiIndex.from_product([SCENARIOS, ops], names=s.index.names))
    s.to_csv(DERIVED / "gap_summary.csv")
    print(s.to_string())
    for sc in SCENARIOS:
        for op in OPERATORS:
            worst = t[(t.scenario == sc) & (t.operator == op)].nlargest(3, "at_risk")
            print(f"\n{sc}, {op}: most residents at risk")
            print(worst[["msoa_name", "indoor_pct", "best_rival_pct", "gap_pp", "at_risk"]].to_string(index=False))


if __name__ == "__main__":
    main()
