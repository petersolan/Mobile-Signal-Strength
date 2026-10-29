"""Opportunity scoring: which coverage gaps matter most, by the customers behind them.

Every address takes the Census 2021 profile of its LSOA (scripts/fetch_demographics.py):
  customers   residents x adult share: people who hold a phone contract
  value       share of residents employed or a full-time student, relative to the area average: the
              data-heavy, mostly pay-monthly customers (retired people use less data on average)
Two plays per operator and neighbourhood (MSOA), from indoor 4G (scripts/gap_analysis.py):
  defend      customers a rival covers indoors and this operator does not: fix coverage (site plan)
  attack      customers only this operator covers indoors: market there
Each scored as customers x value, so 100 = 100 adults of average value. Both coverage scenarios of
gap_analysis.py: as modelled, and bias-corrected.

Writes data/derived/opportunity_msoa.csv.
"""
import pandas as pd

from config import DERIVED
from gap_analysis import OPERATORS, SCENARIOS, indoor, load_addresses


def customer_value(a):
    """Per address: customers (adult residents) and their value relative to the area average, from the
    Census profile of the address's LSOA."""
    demo = pd.read_csv(DERIVED / "lsoa_demographics.csv", index_col="lsoa21cd")
    d = demo.reindex(a.lsoa21cd).set_index(a.index)
    w = demo.residents / demo.residents.sum()
    area_active = ((demo.employed_share + demo.student_share) * w).sum() / (demo.adult_share * w).sum()
    customers = a.population * d.adult_share
    value = (d.employed_share + d.student_share) / d.adult_share / area_active
    return customers, value, d


def score(a):
    """Defend and attack customers and scores per MSOA and operator, for one scenario's addresses."""
    customers, value, d = customer_value(a)
    cov = pd.DataFrame({op: indoor(a, src) for op, src in OPERATORS.items()})
    rows = []
    for op in OPERATORS:
        rivals = cov.drop(columns=op).any(axis=1)
        defend, attack = ~cov[op] & rivals, cov[op] & ~rivals
        rows.append(pd.DataFrame({
            "msoa21cd": a.msoa21cd, "msoa_name": a.msoa_name, "operator": op,
            "defend_customers": customers * defend, "defend_score": customers * value * defend,
            "attack_customers": customers * attack, "attack_score": customers * value * attack,
            "residents": a.population, "students": a.population * d.student_share,
            "employed": a.population * d.employed_share}))
    t = pd.concat(rows).groupby(["msoa21cd", "msoa_name", "operator"], as_index=False).sum()
    t["student_pct"] = 100 * t.students / t.residents
    t["employed_pct"] = 100 * t.employed / t.residents
    return t.drop(columns=["students", "employed"])


def main():
    t = pd.concat([score(load_addresses(sc)).assign(scenario=sc) for sc in SCENARIOS]).round(1)
    t = t.sort_values(["scenario", "operator", "defend_score"], ascending=[True, True, False])
    t.to_csv(DERIVED / "opportunity_msoa.csv", index=False)

    cols = ["msoa_name", "defend_customers", "defend_score", "student_pct", "employed_pct"]
    for sc in SCENARIOS:
        for op in OPERATORS:
            o = t[(t.scenario == sc) & (t.operator == op)]
            print(f"\n{sc}, {op}: defend {o.defend_customers.sum():,.0f} customers "
                  f"(score {o.defend_score.sum():,.0f}), attack {o.attack_customers.sum():,.0f} "
                  f"(score {o.attack_score.sum():,.0f})")
            print(o.nlargest(3, "defend_score")[cols].to_string(index=False))


if __name__ == "__main__":
    main()
