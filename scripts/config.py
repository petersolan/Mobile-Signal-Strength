"""Shared settings for the pipeline."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ARCHIVE = DATA / "archive"        # raw Ofcom zips (gitignored)
EXTRACTS = DATA / "exeter"        # bbox extracts (gitignored)
BASEMAP = DATA / "basemap"        # raw Overpass response (gitignored)
DERIVED = DATA / "derived"        # small outputs, committed
# Census 2021 residents per address (UPRN), from the Census-distribution project (read only)
CENSUS = ROOT.parent / "Census-distribution" / "data" / "processed"
MSOA_NAMES = "https://houseofcommonslibrary.github.io/msoanames/MSOA-Names-2.2.csv"
# EA 1 m LiDAR tiles (DTM, first-return DSM) from the Exeter 3D City Model project (read only)
LIDAR = ROOT.parent / "LiDAR-Building-Footprints" / "data" / "height"
LIDAR_WCS = {
    "dtm": "https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs"
           "?service=WCS&version=2.0.1&request=GetCoverage&format=image/tiff"
           "&CoverageId=13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m",
    "dsm": "https://environment.data.gov.uk/spatialdata/lidar-composite-digital-surface-model-first-return-dsm-1m/wcs"
           "?service=WCS&version=2.0.1&request=GetCoverage&format=image/tiff"
           "&CoverageId=df4e3ec3-315e-48aa-aaaf-b5ae74d7b2bb__Lidar_Composite_Elevation_FZ_DSM_1m",
}
SURFACE_BOUNDS = (285000, 84000, 300000, 100000)   # OS grid west, south, east, north of the coverage grid

# Exeter study area, WGS84 (south, west, north, east)
BBOX = (50.65, -3.62, 50.78, -3.42)

OFCOM = "https://static.ofcom.org.uk/static/research/mobile-signal-strength-data"
# Drive-test releases that contain readings inside BBOX (2020 and 2021 4G were checked: none)
RELEASES = {
    "4g-lte-2022": f"{OFCOM}/4g-lte-2022-mobile-signal-measurement-data.zip",
    "4g-lte-2023": f"{OFCOM}/4g-lte-2023-mobile-signal-measurement-data.zip",
    "4g-lte-2024": f"{OFCOM}/4g-lte-2024-mobile-signal-measurement-data.zip",
    "5g-nr-oct22-dec22": f"{OFCOM}/5g-nr-oct22-dec22-mobile-signal-measurement-data.zip",
    "5g-nr-sep23": f"{OFCOM}/5g-nr-sep23-mobile-signal-measurement-data.zip",
    "5g-nr-2024": f"{OFCOM}/5g-nr-2024-mobile-signal-measurement-data.zip",
}
