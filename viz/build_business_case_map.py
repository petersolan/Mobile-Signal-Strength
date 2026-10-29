"""Map for the README's business case: per operator, the customers a rival covers indoors and it does not
(bias-corrected scenario), and its recommended new sites, numbered in build order and coloured by whether
they break even. Writes docs/images/business_case.png.
"""
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.collections import LineCollection, PolyCollection
from pyproj import Transformer

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here.parent / "scripts"))
from config import DERIVED  # noqa: E402
from gap_analysis import OPERATORS, indoor, load_addresses  # noqa: E402
from site_plan import MARKET_SHARE  # noqa: E402

SCENARIO = "bias-corrected"
COLOURS = {"EE": "#0f9d58", "VMO2": "#1a56db", "VodafoneThree": "#e60000"}
GOOD, MARGINAL, POOR = "#1f2328", "#f2b705", "#c3c8ce"


def basemap(ax, layers, to_os):
    """OSM water, green space and roads, re-projected to the OS grid."""
    def coords(c):
        x, y = to_os.transform(*np.asarray(c).T)
        return np.column_stack([x, y])
    for name, colour in (("green", "#e3ecdf"), ("water", "#cfe0ef")):
        polys = [coords(f["geometry"]["coordinates"][0]) for f in layers[name]["features"]]
        ax.add_collection(PolyCollection(polys, facecolor=colour, edgecolor="none", zorder=0))
    for name, width in (("minor", 0.25), ("mid", 0.5), ("major", 0.8), ("motorway", 1.1)):
        lines = [coords(f["geometry"]["coordinates"]) for f in layers[name]["features"]]
        ax.add_collection(LineCollection(lines, colors="#c9ced4", linewidths=width, zorder=1))


def main():
    a = load_addresses(SCENARIO)
    cov = pd.DataFrame({op: indoor(a, src) for op, src in OPERATORS.items()})
    sites = pd.read_csv(DERIVED / "site_plan.csv")
    sites = sites[sites.scenario == SCENARIO]
    layers = json.loads((DERIVED / "exeter_map.json").read_text(encoding="utf-8"))["basemap"]
    to_os = Transformer.from_crs(4326, 27700, always_xy=True)

    x0, x1 = np.percentile(a.x, [0.5, 99.5])
    y0, y1 = np.percentile(a.y, [0.5, 99.5])
    x0, x1 = min(x0, sites.x.min() - 600), max(x1, sites.x.max() + 600)   # every site in frame
    y0, y1 = min(y0, sites.y.min() - 600), max(y1, sites.y.max() + 600)
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.6), dpi=110)
    for ax, op in zip(axes, OPERATORS):
        basemap(ax, layers, to_os)
        risk = a[~cov[op] & cov.drop(columns=op).any(axis=1)]
        ax.scatter(risk.x, risk.y, s=np.clip(risk.population, 1, 6) * 0.6, color=COLOURS[op], alpha=0.35,
                   linewidths=0, zorder=2)
        for s in sites[sites.operator == op].itertuples():
            pct = s.break_even_pct
            colour = GOOD if pct <= 100 * MARKET_SHARE else MARGINAL if pct <= 50 else POOR
            ax.scatter(s.x, s.y, s=260, marker="*", color=colour, edgecolor="black", linewidths=0.7, zorder=4)
            ax.annotate(str(s.rank), (s.x, s.y), xytext=(7, 5), textcoords="offset points", fontsize=10,
                        fontweight="bold", zorder=5)
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(f"{op}: {int(risk.population.sum()):,} residents at risk", fontsize=12, loc="left")
        for side in ax.spines.values():
            side.set_color("#dde2e7")
    handles = [plt.Line2D([], [], marker="*", ls="", ms=14, mec="black", mfc=c, label=text) for c, text in (
        (GOOD, f"site breaks even below the operator's ~{MARKET_SHARE:.0%} share"),
        (MARGINAL, "marginal: needs 30-50% of the customers it covers"), (POOR, "does not pay on coverage alone"))]
    handles.append(plt.Line2D([], [], marker="o", ls="", ms=5, mfc="#777", mec="none", alpha=0.5,
                              label="address where a rival has indoor 4G and the operator does not"))
    fig.legend(handles=handles, loc="lower center", ncol=2, frameon=False, fontsize=10)
    fig.suptitle("Where each operator loses customers on indoor 4G, and where to build (bias-corrected model)",
                 fontsize=13, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.08, 1, 0.97))
    out = here.parent / "docs" / "images" / "business_case.png"
    fig.savefig(out, facecolor="white")
    print(out, f"{out.stat().st_size / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
