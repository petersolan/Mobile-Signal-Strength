"""Build GeoJSON layers for the MapLibre view.

basemap: OSM (Overpass `out geom` JSON, data/basemap/osm_exeter.json) -> simplified roads / rail / water / green.
cells:   data/derived/exeter_grid_100m.gpkg -> one WGS84 polygon per 100 m cell with a property per
         tech/operator/year, e.g. r_4G_ee_all (median RSRP) and p_4G_ee_all (passes).
Writes data/derived/exeter_map.json = {"basemap": {...}, "cells": FeatureCollection}.
"""
import json

import geopandas as gpd
from shapely.geometry import LineString, Polygon, mapping
from shapely.ops import polygonize, unary_union

from config import BASEMAP, DERIVED

osm = json.loads((BASEMAP / "osm_exeter.json").read_text(encoding="utf-8"))

MAJOR = {"motorway", "trunk", "primary", "motorway_link", "trunk_link", "primary_link"}
MID = {"secondary", "secondary_link", "tertiary"}
groups = {k: [] for k in ["motorway", "major", "mid", "minor", "rail", "river", "water", "green"]}


def line(el):
    return LineString([(p["lon"], p["lat"]) for p in el["geometry"]]) if len(el.get("geometry", [])) > 1 else None


for el in osm["elements"]:
    t = el.get("tags", {})
    if el["type"] == "way":
        g = line(el)
        if g is None:
            continue
        hw = t.get("highway")
        if hw:
            key = "motorway" if hw.startswith("motorway") else "major" if hw in MAJOR else "mid" if hw in MID else "minor"
            groups[key].append(g)
        elif t.get("railway") == "rail":
            groups["rail"].append(g)
        elif t.get("waterway"):
            groups["river"].append(g)
        elif g.is_ring:
            groups["water" if t.get("natural") == "water" else "green"].append(Polygon(g.coords))
    elif el["type"] == "relation":
        outers = [LineString([(p["lon"], p["lat"]) for p in m["geometry"]])
                  for m in el.get("members", []) if m.get("role") == "outer" and len(m.get("geometry", [])) > 1]
        groups["water"].extend(polygonize(unary_union(outers)))

TOL = {"motorway": 2e-5, "major": 2e-5, "mid": 3e-5, "minor": 4e-5, "rail": 3e-5, "river": 3e-5, "water": 3e-5, "green": 4e-5}


def fc(geoms, tol):
    feats = []
    for g in geoms:
        s = g.simplify(tol, preserve_topology=True)
        if s.is_empty or (s.geom_type == "Polygon" and s.area < 2e-8):
            continue
        feats.append({"type": "Feature", "properties": {}, "geometry": json.loads(json.dumps(mapping(s)), parse_float=lambda x: round(float(x), 5))})
    return {"type": "FeatureCollection", "features": feats}


basemap = {k: fc(v, TOL[k]) for k, v in groups.items()}
for k, v in basemap.items():
    print(f"{k}: {len(v['features'])}")

g = gpd.read_file(DERIVED / "exeter_grid_100m.gpkg", layer="cells")
g["key"] = g.tech + "_" + g.operator + "_" + g.year.astype(str)
wide_r = g.pivot_table(index=["cx", "cy"], columns="key", values="rsrp").add_prefix("r_")
wide_p = g.pivot_table(index=["cx", "cy"], columns="key", values="passes").add_prefix("p_")
geom = g.drop_duplicates(["cx", "cy"]).set_index(["cx", "cy"]).geometry
cells = gpd.GeoDataFrame(wide_r.join(wide_p), geometry=geom.reindex(wide_r.index), crs=27700).to_crs(4326).reset_index()

feats = []
for row in cells.itertuples(index=False):
    d = row._asdict()
    props = {"cx": int(d["cx"]), "cy": int(d["cy"])}
    for k, v in d.items():
        if k[:2] in ("r_", "p_") and v == v:  # skip NaN
            props[k] = round(float(v), 1) if k.startswith("r_") else int(v)
    coords = [[round(x, 6), round(y, 6)] for x, y in d["geometry"].exterior.coords]
    feats.append({"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [coords]}})

out = DERIVED / "exeter_map.json"
out.write_text(json.dumps({"basemap": basemap, "cells": {"type": "FeatureCollection", "features": feats}}, separators=(",", ":")))
print(f"cells: {len(feats)}; json {out.stat().st_size / 1e6:.2f} MB")
