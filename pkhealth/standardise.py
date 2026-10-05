"""Turn each raw source into one clean table with a shared schema."""

import difflib
import json
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from shapely import STRtree
from shapely.geometry import Point, shape

# OSM amenity / healthcare tags -> the facility types used in the graph
FACILITY_TYPES = {
    "hospital": "hospital",
    "clinic": "clinic",
    "doctors": "doctors",
    "dentist": "dentist",
    "pharmacy": "pharmacy",
    "laboratory": "laboratory",
}

# spelling variants and near-synonyms found in the healthcare:speciality tag
SPECIALTY_ALIASES = {
    "pediatrics": "paediatrics",
    "gynecology": "gynaecology",
    "obstetrics": "gynaecology",
    "orthopedics": "orthopaedics",
    "dentist": "dentistry",
    "dental": "dentistry",
    "orthodontics": "dentistry",
    "diagnostic_radiology": "radiology",
    "radiotherapy": "oncology",
    "general_practice": "general",
    "family_medicine": "general",
}


def clean_name(value):
    if not isinstance(value, str):
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def facility_type(props):
    for tag in ("amenity", "healthcare"):
        t = FACILITY_TYPES.get(str(props.get(tag) or "").lower())
        if t:
            return t
    return "other"


def specialties(value):
    if not isinstance(value, str) or not value.strip():
        return []
    out = []
    for part in re.split(r"[;,]", value.lower()):
        part = part.strip().replace(" ", "_")
        if part:
            part = SPECIALTY_ALIASES.get(part, part)
            if part not in out:
                out.append(part)
    return out


def _read_geojson_zip(path):
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.endswith(".geojson"))
        return json.loads(z.read(name))["features"]


def load_facilities(point_zip, polygon_zip):
    rows = []
    for path, kind in ((point_zip, "point"), (polygon_zip, "polygon")):
        for feat in _read_geojson_zip(path):
            p = feat["properties"]
            geom = shape(feat["geometry"])
            pt = geom if kind == "point" else geom.representative_point()
            osm_type = {"nodes": "node", "ways_poly": "way", "relations": "relation"}.get(p.get("osm_type"), p.get("osm_type"))
            rows.append({
                "osm_id": f"{osm_type}/{p['osm_id']}",
                "geometry_kind": kind,
                "name": clean_name(p.get("name:en")) or clean_name(p.get("name")),
                "name_ur": clean_name(p.get("name:ur")),
                "type": facility_type(p),
                "specialties": specialties(p.get("healthcare:speciality")),
                "operator_type": clean_name(p.get("operator:type")),
                "city": clean_name(p.get("addr:city")),
                "lon": pt.x,
                "lat": pt.y,
            })
    df = pd.DataFrame(rows)
    df["city"] = df["city"].str.title()
    return df


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def deduplicate(df, radius_m=150):
    """Drop records that are the same place mapped twice (e.g. a node and a building outline).

    Two records are treated as duplicates when their normalised names are equal
    and they lie within `radius_m` metres of each other. The record with more
    filled fields is kept. Returns (kept, dropped) so the choice can be audited.
    """
    df = df.copy()
    df["_filled"] = df[["name", "name_ur", "operator_type", "city"]].notna().sum(axis=1) + df["specialties"].str.len()
    df["_key"] = df["name"].map(lambda n: _norm(n) if n else None)
    lat_m, lon_m = 111_320, 111_320 * np.cos(np.radians(30))  # Pakistan sits around 30 N
    drop = set()
    for _, g in df[df["_key"].notna()].groupby("_key"):
        if len(g) < 2:
            continue
        g = g.sort_values("_filled", ascending=False)
        kept = []
        for idx, r in g.iterrows():
            close = any(
                np.hypot((r.lat - k.lat) * lat_m, (r.lon - k.lon) * lon_m) <= radius_m for k in kept
            )
            if close:
                drop.add(idx)
            else:
                kept.append(r)
    dropped = df.loc[sorted(drop)]
    kept = df.drop(index=list(drop)).drop(columns=["_filled", "_key"])
    return kept.reset_index(drop=True), dropped.drop(columns=["_filled", "_key"])


def load_districts(geojson_path):
    feats = json.loads(Path(geojson_path).read_text(encoding="utf-8"))["features"]
    return pd.DataFrame({
        "district": [f["properties"]["shapeName"] for f in feats],
        "district_id": [f["properties"]["shapeID"] for f in feats],
        "geometry": [shape(f["geometry"]) for f in feats],
    })


def assign_districts(facilities, districts, max_snap_m=5000):
    """Point-in-polygon, with a small snap distance for points just off a border or coast."""
    tree = STRtree(districts["geometry"].to_list())
    names = districts["district"].to_numpy()
    out, how = [], []
    for lon, lat in zip(facilities["lon"], facilities["lat"]):
        pt = Point(lon, lat)
        hits = tree.query(pt, predicate="within")
        if len(hits):
            out.append(names[hits[0]])
            how.append("inside")
            continue
        nearest = tree.nearest(pt)
        dist_m = districts["geometry"].iloc[nearest].distance(pt) * 111_000
        if dist_m <= max_snap_m:
            out.append(names[nearest])
            how.append("snapped")
        else:
            out.append(None)
            how.append("outside")
    facilities = facilities.copy()
    facilities["district"] = out
    facilities["district_match"] = how
    return facilities


def load_population(csv_path):
    pop = pd.read_csv(csv_path, encoding="utf-8-sig")
    return pop.rename(columns={
        "admin2Name_en": "district", "admin2Pcode": "pcode",
        "admin1Name_en": "province", "T_TL": "population",
    })[["district", "pcode", "province", "population"]]


def _key(name):
    name = str(name).lower().replace("&", "and")
    name = re.sub(r"\b(district|agency|tribal|capital territory)\b", "", name)
    return re.sub(r"[^a-z]", "", name)


def match_names(left, right, aliases=None, excluded=None, cutoff=0.88):
    """Map every name in `left` to at most one name in `right`, one-to-one.

    First pass: documented exclusions, manual aliases and exact matches on a
    normalised key. Second pass: fuzzy matching, only against names not
    already taken, so two districts can never share one census row. Returns a
    DataFrame with the method used, so every fuzzy decision can be checked.
    """
    aliases, excluded = aliases or {}, set(excluded or [])
    right_keys = {_key(r): r for r in right}
    result, taken = {}, set()
    for name in left:
        if name in excluded:
            result[name] = (None, "excluded", 0.0)
        elif name in aliases:
            result[name] = (aliases[name], "alias", 1.0)
        elif _key(name) in right_keys:
            result[name] = (right_keys[_key(name)], "exact", 1.0)
        else:
            continue
        if result[name][0]:
            taken.add(result[name][0])
    for name in left:
        if name in result:
            continue
        free = {k: r for k, r in right_keys.items() if r not in taken}
        close = difflib.get_close_matches(_key(name), list(free), n=1, cutoff=cutoff)
        if close:
            score = difflib.SequenceMatcher(None, _key(name), close[0]).ratio()
            result[name] = (free[close[0]], "fuzzy", round(score, 3))
            taken.add(free[close[0]])
        else:
            result[name] = (None, "unmatched", 0.0)
    rows = [(name, *result[name]) for name in left]
    return pd.DataFrame(rows, columns=["left", "right", "method", "score"])


def load_who(json_path, indicator):
    values = json.loads(Path(json_path).read_text(encoding="utf-8"))["value"]
    rows = [(v["TimeDim"], v["NumericValue"]) for v in values if v.get("NumericValue") is not None]
    df = pd.DataFrame(rows, columns=["year", "value"]).sort_values("year")
    df["indicator"] = indicator
    return df
