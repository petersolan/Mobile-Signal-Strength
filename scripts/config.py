"""Shared settings for the pipeline."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ARCHIVE = DATA / "archive"        # raw Ofcom zips (gitignored)
EXTRACTS = DATA / "exeter"        # bbox extracts (gitignored)
BASEMAP = DATA / "basemap"        # raw Overpass response (gitignored)
DERIVED = DATA / "derived"        # small outputs, committed

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
