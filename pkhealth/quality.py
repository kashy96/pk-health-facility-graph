"""Data-quality checks.

Each check returns a row: what was checked, the measured value, the threshold
and whether it passed. "error" checks stop the pipeline; "warn" checks are
reported but do not, because some problems (e.g. missing specialty tags) are
properties of the open data itself and are part of what this project measures.
"""

import pandas as pd

# roughly Pakistan's bounding box
LAT_RANGE = (23.5, 37.2)
LON_RANGE = (60.8, 77.9)


def _row(check, value, threshold, passed, severity, detail=""):
    return {"check": check, "value": value, "threshold": threshold, "passed": bool(passed), "severity": severity, "detail": detail}


def facility_checks(fac, cfg):
    rows = []
    n = len(fac)
    dup = fac["osm_id"].duplicated().sum()
    rows.append(_row("facility ids are unique", int(dup), 0, dup == 0, "error", "duplicate osm_id values"))

    bad_coords = (~fac["lat"].between(*LAT_RANGE) | ~fac["lon"].between(*LON_RANGE)).sum()
    rows.append(_row("coordinates inside Pakistan's bounding box", int(bad_coords), 0, bad_coords == 0, "error"))

    for col, threshold in cfg["completeness"].items():
        if col == "specialties":
            share = (fac[col].str.len() > 0).mean()
        else:
            share = fac[col].notna().mean()
        rows.append(_row(f"completeness of `{col}`", round(share, 3), threshold, share >= threshold, "warn",
                         f"{share:.1%} of {n} facilities"))

    other = (fac["type"] == "other").mean()
    rows.append(_row("facility type recognised", round(1 - other, 3), 0.9, other <= 0.1, "warn",
                     f"{other:.1%} could not be mapped to a type"))

    outside = (fac["district_match"] == "outside").sum()
    snapped = (fac["district_match"] == "snapped").sum()
    rows.append(_row("facilities placed in a district", int(outside), 0, outside == 0, "warn",
                     f"{snapped} snapped to the nearest district within 5 km, {outside} left unassigned"))

    # does the free-text city agree with the district the coordinates fall in?
    with_city = fac[fac["city"].notna() & fac["district"].notna()]
    agree = (with_city["city"].str.lower().str.replace(" ", "") ==
             with_city["district"].str.lower().str.replace(" ", "")).mean()
    rows.append(_row("addr:city agrees with the coordinate district", round(agree, 3), None, True, "info",
                     f"{agree:.1%} of {len(with_city)} facilities that have a city; low values are expected, "
                     "since a city is not a district, but large gaps flag misplaced points"))
    return rows


def join_checks(matches, label, min_share):
    eligible = matches[matches["method"] != "excluded"]
    matched = eligible["method"] != "unmatched"
    share = matched.mean()
    fuzzy = (eligible["method"] == "fuzzy").sum()
    missing = ", ".join(eligible.loc[~matched, "left"].astype(str).head(12))
    n_excluded = len(matches) - len(eligible)
    return [_row(f"{label}: names matched", round(share, 3), min_share, share >= min_share, "warn",
                 f"{fuzzy} fuzzy matches to review; {n_excluded} excluded by design; unmatched: {missing or 'none'}")]


def run(fac, district_matches, cfg):
    rows = facility_checks(fac, cfg["quality"])
    rows += join_checks(district_matches, "boundaries -> population", cfg["quality"]["min_join_share"])
    report = pd.DataFrame(rows)
    failed = report[(report["severity"] == "error") & ~report["passed"]]
    if len(failed):
        raise SystemExit("quality checks failed:\n" + failed.to_string(index=False))
    return report
