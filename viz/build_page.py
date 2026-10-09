"""Assemble the single-file site docs/index.html (served by GitHub Pages) from the template,
the MapLibre stylesheet and the derived data."""
from pathlib import Path

here = Path(__file__).resolve().parent
derived = here.parent / "data" / "derived"
page = (here / "atlas.template.html").read_text(encoding="utf-8")
page = page.replace("__MAPLIBRE_CSS__", (here / "maplibre-gl-4.7.1.css").read_text(encoding="utf-8"))
page = page.replace("__DATA__", (derived / "exeter_grid_100m.json").read_text(encoding="utf-8"))
page = page.replace("__GEO__", (derived / "exeter_map.json").read_text(encoding="utf-8"))
HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="Ofcom drive-test 4G and 5G signal strength on Exeter's roads, 2022-2024, mapped on a 100 m OS grid by operator and year.">
<style>:root{color-scheme:light}body{margin:0}[hidden]{display:none!important}img{max-width:100%}</style>
"""
page = HEAD + page + "\n</html>\n"
out = here.parent / "docs" / "index.html"
out.parent.mkdir(exist_ok=True)
out.write_text(page, encoding="utf-8")
print(f"{out.name}: {out.stat().st_size / 1e6:.2f} MB")
