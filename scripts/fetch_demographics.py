"""Census 2021 profile of every LSOA in the coverage area, from the Nomis API (ONS, open data): adults,
people in employment, full-time students and retired people, as shares of all residents. Used to weight
coverage gaps by the customers behind them (scripts/opportunity.py).

Writes data/derived/lsoa_demographics.csv.
"""
import io

import pandas as pd
import requests

from config import DERIVED

NOMIS = "https://www.nomisweb.co.uk/api/v01/dataset"
# TS066 economic activity status (residents aged 16+), codes of C2021_EASTAT_20
ACTIVITY = {"0": "adults", "1002": "employed", "1007": "employed_student", "1006": "student_active",
            "16": "student_inactive", "15": "retired"}


def nomis(dataset, dimension, codes, lsoas):
    """One Census table for the given LSOAs, wide: one column per category code."""
    rows = []
    for i in range(0, len(lsoas), 100):   # keep the URL short
        r = requests.get(f"{NOMIS}/{dataset}.data.csv", timeout=60, params={
            "geography": ",".join(lsoas[i:i + 100]), dimension: ",".join(codes), "measures": "20100",
            "select": f"geography_code,{dimension}_code,obs_value"})
        r.raise_for_status()
        rows.append(pd.read_csv(io.StringIO(r.text)))
    t = pd.concat(rows)
    t[f"{dimension}_CODE"] = t[f"{dimension}_CODE"].astype(str).str.lstrip("_")   # Nomis writes some as _15
    return t.pivot(index="GEOGRAPHY_CODE", columns=f"{dimension}_CODE", values="OBS_VALUE")


def main():
    lsoas = sorted(pd.read_parquet(DERIVED / "coverage_addresses.parquet", columns=["lsoa21cd"]).lsoa21cd.unique())
    act = nomis("NM_2083_1", "C2021_EASTAT_20", list(ACTIVITY), lsoas)
    act.columns = [ACTIVITY[c] for c in act.columns]
    total = nomis("NM_2020_1", "C2021_AGE_19", ["0"], lsoas)["0"].rename("residents")
    t = act.join(total)
    d = pd.DataFrame({
        "residents": t.residents,
        "adult_share": t.adults / t.residents,
        "employed_share": (t.employed + t.employed_student) / t.residents,
        "student_share": (t.student_active + t.student_inactive) / t.residents,
        "retired_share": t.retired / t.residents,
    }).round(4)
    d.index.name = "lsoa21cd"
    d.to_csv(DERIVED / "lsoa_demographics.csv")
    print(f"{len(d)} LSOAs of {len(lsoas)}; shares across the area:")
    w = d.residents / d.residents.sum()
    print(d.drop(columns="residents").mul(w, axis=0).sum().round(3).to_string())


if __name__ == "__main__":
    main()
