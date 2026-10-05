"""Run the whole pipeline: download -> standardise -> check -> graph -> queries -> report.

    python -m pkhealth.pipeline            # uses cached raw files if present
    python -m pkhealth.pipeline --refresh  # download everything again
"""

import argparse
import json
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402
from matplotlib.collections import PatchCollection  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402

from . import graph, quality, sources, standardise  # noqa: E402


def md_table(df, floatfmt="{:.2f}"):
    if df.empty:
        return "_none_"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.itertuples(index=False):
        cells = [floatfmt.format(v) if isinstance(v, float) else ("" if v is None else str(v)) for v in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def plot_map(districts, value_col, title, path, cmap="viridis"):
    fig, ax = plt.subplots(figsize=(7, 7.5))
    patches, values = [], []
    for geom, value in zip(districts["geometry"], districts[value_col]):
        polys = getattr(geom, "geoms", [geom])
        for poly in polys:
            patches.append(MplPolygon(np.asarray(poly.exterior.coords), closed=True))
            values.append(value)
    values = np.asarray(values, dtype=float)
    pc = PatchCollection(patches, cmap=cmap, edgecolor="white", linewidth=0.3)
    pc.set_array(np.ma.masked_invalid(values))
    pc.cmap.set_bad("#dddddd")
    ax.add_collection(pc)
    ax.autoscale_view()
    ax.set_aspect("equal")
    ax.axis("off")
    fig.colorbar(pc, ax=ax, shrink=0.6, label=value_col.replace("_", " "))
    ax.set_title(title + "\n(grey = no population match)", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_completeness(fac, path):
    shares = {
        "name": fac["name"].notna().mean(),
        "type recognised": (fac["type"] != "other").mean(),
        "city": fac["city"].notna().mean(),
        "Urdu name": fac["name_ur"].notna().mean(),
        "operator type": fac["operator_type"].notna().mean(),
        "specialty": (fac["specialties"].str.len() > 0).mean(),
    }
    s = pd.Series(shares).sort_values()
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.barh(s.index, s.values * 100)
    for i, v in enumerate(s.values):
        ax.text(v * 100 + 1, i, f"{v:.0%}", va="center", fontsize=9)
    ax.set_xlim(0, 110)
    ax.set_xlabel("% of facilities with the field filled")
    ax.set_title("How complete is the open data?")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--refresh", action="store_true", help="download the sources again")
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    t0 = time.time()

    raw = Path(cfg["raw_dir"])
    out = Path(cfg["output_dir"])
    (out / "figures").mkdir(parents=True, exist_ok=True)
    (out / "tables").mkdir(parents=True, exist_ok=True)
    manifest = sources.download_all(cfg["sources"], raw, refresh=args.refresh)
    src = {k: raw / v["file"] for k, v in cfg["sources"].items()}

    # standardise
    fac = standardise.load_facilities(src["facilities_points"], src["facilities_polygons"])
    n_raw = len(fac)
    fac, dropped = standardise.deduplicate(fac, cfg["dedupe_radius_m"])
    districts = standardise.load_districts(src["districts"])
    fac = standardise.assign_districts(fac, districts)
    pop = standardise.load_population(src["population"])
    matches = standardise.match_names(districts["district"], pop["district"],
                                       cfg.get("district_aliases"), cfg.get("not_in_census"))
    districts = districts.merge(matches.rename(columns={"left": "district", "right": "pop_district"}), on="district")
    districts = districts.merge(pop.rename(columns={"district": "pop_district"}), on="pop_district", how="left")
    who = pd.concat([standardise.load_who(src[k], k) for k in ("who_doctors_per_10k", "who_nurses_per_10k")])

    # quality
    report = quality.run(fac, matches, cfg)
    report.to_csv(out / "quality_report.csv", index=False)

    # graph + queries
    latest = who.sort_values("year").groupby("indicator").tail(1)
    country = list(latest[["indicator", "year", "value"]].itertuples(index=False, name=None))
    g = graph.build(fac, districts, country)
    g.serialize(out / "graph.ttl", format="turtle")
    tables = graph.run_queries(g, "queries")
    for name, df in tables.items():
        df.to_csv(out / "tables" / f"{name}.csv", index=False)

    # figures
    per_district = fac[fac["type"] == "hospital"].groupby("district").size()
    districts["hospitals"] = districts["district"].map(per_district).fillna(0)
    districts["hospitals_per_100k"] = districts["hospitals"] / districts["population"] * 1e5
    plot_map(districts, "hospitals_per_100k", "Hospitals in OpenStreetMap per 100,000 people",
             out / "figures" / "hospitals_per_100k.png")
    plot_completeness(fac, out / "figures" / "completeness.png")

    # summary
    fac.drop(columns=["specialties"]).assign(specialties=fac["specialties"].str.join(";")).to_csv(
        out / "tables" / "facilities_clean.csv", index=False)
    matches.to_csv(out / "tables" / "district_name_matches.csv", index=False)
    per100 = tables["01_hospitals_per_100k"]
    stats = {
        "facilities_raw": n_raw,
        "duplicates_removed": len(dropped),
        "facilities_clean": len(fac),
        "by_type": fac["type"].value_counts().to_dict(),
        "districts": len(districts),
        "districts_matched_to_population": int(matches["method"].isin(["exact", "alias", "fuzzy"]).sum()),
        "districts_excluded": int((matches["method"] == "excluded").sum()),
        "districts_without_hospital": len(tables["02_districts_without_hospital"]),
        "triples": len(g),
        "who_latest": {r.indicator: {"year": int(r.year), "value": r.value} for r in latest.itertuples()},
    }
    (out / "summary.json").write_text(json.dumps(stats, indent=2, default=str))

    lines = [
        "# Results",
        "",
        "Generated by `python -m pkhealth.pipeline`. Source versions are in `data/raw/manifest.json`.",
        "",
        "## Pipeline",
        "",
        f"- Raw facility records: **{n_raw}**; duplicates removed: **{len(dropped)}**; clean facilities: **{len(fac)}**",
        f"- Facility types: {', '.join(f'{k} {v}' for k, v in stats['by_type'].items())}",
        f"- Districts: **{len(districts)}**, of which **{stats['districts_matched_to_population']}** matched to census population",
        f"- Knowledge graph: **{len(g):,}** triples (`results/graph.ttl`)",
        f"- WHO national context: " + "; ".join(
            f"{k.replace('who_', '').replace('_', ' ')} = {v['value']:.1f} ({v['year']})" for k, v in stats["who_latest"].items()),
        "",
        "## Data quality",
        "",
        md_table(report[["check", "value", "threshold", "passed", "severity", "detail"]], "{:.3f}"),
        "",
        "## District name matching (boundaries 2019 vs census 2017)",
        "",
        md_table(matches[matches["method"].isin(["alias", "fuzzy", "unmatched", "excluded"])], "{:.3f}"),
        "",
        "## Lowest and highest mapped hospital density",
        "",
        "Lowest 10:",
        "",
        md_table(per100.head(10)),
        "",
        "Highest 10:",
        "",
        md_table(per100.tail(10).iloc[::-1]),
        "",
        f"## Districts with no hospital in OpenStreetMap ({len(tables['02_districts_without_hospital'])})",
        "",
        md_table(tables["02_districts_without_hospital"]),
        "",
        "## Facility records with no name",
        "",
        md_table(tables["05_unnamed_facilities"]),
        "",
    ]
    (out / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"done in {time.time() - t0:.0f}s: {len(fac)} facilities, {len(g):,} triples -> {out}/RESULTS.md")


if __name__ == "__main__":
    main()
