"""Convert Ofcom mobile signal strength CSVs to Parquet (zstd) with DuckDB."""
import sys
from pathlib import Path

import duckdb

data_dir = Path(sys.argv[1])
out_dir = data_dir / "parquet"
out_dir.mkdir(exist_ok=True)

con = duckdb.connect()
con.execute("SET preserve_insertion_order = false")
for csv in sorted(data_dir.glob("*.csv")):
    out = out_dir / (csv.stem + ".parquet")
    print(f"{csv.name} -> {out.name}", flush=True)
    con.execute(f"""
        COPY (SELECT * FROM read_csv('{csv.as_posix()}', sample_size=-1, nullstr=['', 'n.a.']))
        TO '{out.as_posix()}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 1000000)
    """)
    n = con.execute(f"SELECT count(*) FROM '{out.as_posix()}'").fetchone()[0]
    print(f"  rows: {n:,}  size: {out.stat().st_size/1e6:,.0f} MB", flush=True)
