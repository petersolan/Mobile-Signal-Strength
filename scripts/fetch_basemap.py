"""Fetch OpenStreetMap roads, rail, water and green space for config.BBOX from the Overpass API."""
import urllib.parse
import urllib.request

from config import BASEMAP, BBOX

bb = ",".join(map(str, BBOX))
QUERY = f"""[out:json][timeout:120];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|motorway_link|trunk_link|primary_link|secondary_link|unclassified|residential|living_street)$"]({bb});
  way["railway"="rail"]({bb});
  way["waterway"~"^(river|canal)$"]({bb});
  way["natural"="water"]({bb});
  relation["natural"="water"]({bb});
  way["leisure"~"^(park|nature_reserve|golf_course)$"]({bb});
  way["landuse"~"^(forest|meadow|grass|recreation_ground)$"]({bb});
);
out geom;"""

BASEMAP.mkdir(parents=True, exist_ok=True)
req = urllib.request.Request(
    "https://overpass-api.de/api/interpreter",
    data=urllib.parse.urlencode({"data": QUERY}).encode(),
    headers={"User-Agent": "Mobile-Signal-Strength pipeline", "Accept": "application/json"},
)
with urllib.request.urlopen(req, timeout=180) as r:
    body = r.read()
(BASEMAP / "osm_exeter.json").write_bytes(body)
print(f"osm_exeter.json: {len(body) / 1e6:.1f} MB")
