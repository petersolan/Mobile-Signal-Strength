"""Ground (DTM) and surface (DSM: ground + buildings + trees) heights on a 5 m OS grid for the coverage
model, from Environment Agency 1 m LiDAR composites.

Inputs: 5 km tiles from the Exeter 3D City Model project (config.LIDAR, read only), plus data/lidar/
for the strips it lacks, downloaded from the EA WCS service (see LIDAR_WCS in config.py).
Outputs (data/exeter/): dtm_5m.tif and dsm_5m.tif, EPSG:27700, covering config.SURFACE_BOUNDS.
Each 5 m pixel is the mean of its 1 m pixels; gaps (water, missing LiDAR) take the nearest value, and
the DSM is never below the DTM.
"""
import os

os.environ.pop("PROJ_LIB", None)    # a system-wide PROJ_LIB (PostGIS) breaks rasterio's bundled PROJ
os.environ.pop("PROJ_DATA", None)

import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from scipy.ndimage import distance_transform_edt

from config import DATA, EXTRACTS, LIDAR, LIDAR_WCS, SURFACE_BOUNDS

CELL = 5
EXTRA = DATA / "lidar"
# (kind, west, south, east, north) of the pieces the 3D City Model tiles do not cover
MISSING = [("dtm", x, 84000, x + 5000, 85000) for x in (285000, 290000, 295000)] + \
          [("dsm", x, 84000, x + 5000, 85000) for x in (285000, 290000, 295000)] + \
          [("dsm", 285000, 85000, 290000, 90000)]


def download_missing():
    EXTRA.mkdir(parents=True, exist_ok=True)
    for kind, w, s, e, n in MISSING:
        path = EXTRA / f"{kind}_{w}_{s}.tif"
        if not path.exists():
            urllib.request.urlretrieve(f"{LIDAR_WCS[kind]}&subset=E({w},{e})&subset=N({s},{n})", path)


def mosaic(kind):
    w, s, e, n = SURFACE_BOUNDS
    shape = (int((n - s) / CELL), int((e - w) / CELL))
    out = np.full(shape, np.nan, np.float32)
    dst_t = from_origin(w, n, CELL, CELL)
    tiles = sorted((LIDAR / kind).glob("*.tif")) + sorted(EXTRA.glob(f"{kind}_*.tif"))
    for path in tiles:
        with rasterio.open(path) as src:
            a = src.read(1).astype(np.float32)
            a[(a < -100) | (a > 2000)] = np.nan          # nodata values differ between sources
            tmp = np.full(shape, np.nan, np.float32)
            reproject(a, tmp, src_transform=src.transform, src_crs="EPSG:27700", src_nodata=np.nan,
                      dst_transform=dst_t, dst_crs="EPSG:27700", dst_nodata=np.nan, resampling=Resampling.average)
            fill = np.isnan(out) & ~np.isnan(tmp)
            out[fill] = tmp[fill]
    gaps = np.isnan(out)
    if gaps.any():
        idx = distance_transform_edt(gaps, return_distances=False, return_indices=True)
        out = out[tuple(idx)]
    print(f"{kind}: {len(tiles)} tiles, {100 * gaps.mean():.2f}% gaps filled")
    return out, dst_t


def main():
    download_missing()
    dtm, t = mosaic("dtm")
    dsm, _ = mosaic("dsm")
    dsm = np.maximum(dsm, dtm)
    for name, a in (("dtm_5m.tif", dtm), ("dsm_5m.tif", dsm)):
        with rasterio.open(EXTRACTS / name, "w", driver="GTiff", height=a.shape[0], width=a.shape[1], count=1,
                           dtype="float32", crs="EPSG:27700", transform=t, compress="deflate") as dst:
            dst.write(a, 1)
    print(f"surface {dsm.shape}, ground {np.percentile(dtm, 1):.0f}-{np.percentile(dtm, 99):.0f} m, "
          f"objects up to {np.percentile(dsm - dtm, 99.9):.0f} m")


if __name__ == "__main__":
    main()
