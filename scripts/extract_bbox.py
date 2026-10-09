"""Stream each downloaded zip and keep only rows inside config.BBOX.

The zips hold 1-13 GB CSVs (sometimes several per zip, same header). Rows are filtered on the
first two columns (latitude, longitude) without unpacking to disk.
Usage: python scripts/extract_bbox.py [release ...]
"""
import io
import sys
import zipfile

from config import ARCHIVE, BBOX, EXTRACTS, RELEASES

S, W, N, E = BBOX


def extract(name):
    zpath = ARCHIVE / RELEASES[name].rsplit("/", 1)[1]
    out = EXTRACTS / f"{name}-exeter.csv"
    kept = 0
    with zipfile.ZipFile(zpath) as z, open(out, "w", encoding="utf-8", newline="") as dst:
        header = None
        for member in sorted(m for m in z.namelist() if m.endswith(".csv")):
            with io.TextIOWrapper(z.open(member), encoding="utf-8-sig", newline="") as src:
                h = src.readline()
                if header is None:
                    header = h
                    dst.write(h)
                elif h != header:
                    raise ValueError(f"{member}: header differs from first CSV in {zpath.name}")
                for line in src:
                    lat, lon, _ = line.split(",", 2)
                    try:
                        if S <= float(lat) <= N and W <= float(lon) <= E:
                            dst.write(line)
                            kept += 1
                    except ValueError:
                        continue
    print(f"{name}: {kept:,} rows -> {out.name}", flush=True)


if __name__ == "__main__":
    EXTRACTS.mkdir(parents=True, exist_ok=True)
    for n in sys.argv[1:] or RELEASES:
        extract(n)
