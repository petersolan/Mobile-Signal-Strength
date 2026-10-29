"""Runs sql/business_case.sql in DuckDB and checks it against the pandas outputs of gap_analysis.py and
opportunity.py: two independent implementations of the same analysis must agree to the rounding.

Writes nothing; exits with an error if a figure differs.
"""
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from config import DERIVED

SQL = Path(__file__).resolve().parent.parent / "sql" / "business_case.sql"
CHECKS = {
    "gap_msoa": (["scenario", "msoa21cd", "operator"],
                 ["residents", "indoor", "at_risk", "advantage", "indoor_pct", "best_rival_pct", "gap_pp", "not_spot"]),
    "gap_summary": (["scenario", "operator"], ["residents", "indoor", "at_risk", "advantage", "indoor_pct", "not_spot"]),
    "opportunity_msoa": (["scenario", "msoa21cd", "operator"],
                         ["defend_customers", "defend_score", "attack_customers", "attack_score", "residents",
                          "student_pct", "employed_pct"]),
}


def main():
    con = duckdb.connect()
    con.execute(SQL.read_text(encoding="utf-8").replace("${DERIVED}", DERIVED.as_posix()))
    failed = False
    for table, (keys, values) in CHECKS.items():
        sql = con.table(table).df().set_index(keys).sort_index()
        py = pd.read_csv(DERIVED / f"{table}.csv").set_index(keys).sort_index()
        sql, py = sql.loc[py.index, values].astype(float).fillna(0), py[values].astype(float).fillna(0)
        worst = (sql - py).abs().max()
        ok = len(sql) == len(py) and bool((worst <= 0.11).all())   # both sides round to 0.1
        failed |= not ok
        print(f"{table}: {len(sql)} rows, largest difference {np.round(worst.max(), 3)}: "
              f"{'matches' if ok else 'DIFFERS'}")
        if not ok:
            print(worst[worst > 0.11].to_string())
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
