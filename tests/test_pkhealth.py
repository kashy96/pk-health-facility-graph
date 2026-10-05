import pandas as pd
from shapely.geometry import box

from pkhealth import graph, standardise


def facilities(rows):
    base = {"name_ur": None, "operator_type": None, "city": None, "specialties": [], "type": "hospital", "geometry_kind": "point"}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_specialties_are_normalised():
    assert standardise.specialties("Pediatrics; gynecology;dental") == ["paediatrics", "gynaecology", "dentistry"]
    assert standardise.specialties("general;general") == ["general"]
    assert standardise.specialties(None) == []


def test_facility_type_falls_back_to_healthcare_tag():
    assert standardise.facility_type({"amenity": None, "healthcare": "clinic"}) == "clinic"
    assert standardise.facility_type({"amenity": "hospital"}) == "hospital"
    assert standardise.facility_type({"amenity": "spa"}) == "other"


def test_name_matching_is_one_to_one():
    left = ["Sheikhpura", "Karachi", "Kharmang", "Kharan", "Gilgit"]
    right = ["Sheikhupura", "Karachi City", "Kharan"]
    m = standardise.match_names(left, right, aliases={"Karachi": "Karachi City"}, excluded=["Gilgit"]).set_index("left")
    assert m.loc["Sheikhpura", "method"] == "fuzzy"
    assert m.loc["Karachi", "right"] == "Karachi City"
    assert m.loc["Kharan", "method"] == "exact"
    # Kharan is already taken, so Kharmang must not be forced onto it
    assert m.loc["Kharmang", "method"] == "unmatched"
    assert m.loc["Gilgit", "method"] == "excluded"


def test_deduplicate_only_merges_close_records_with_the_same_name():
    df = facilities([
        {"osm_id": "node/1", "name": "District Hospital", "lat": 30.0, "lon": 70.0},
        {"osm_id": "way/2", "name": "District  Hospital", "lat": 30.0005, "lon": 70.0, "city": "Multan"},  # ~55 m away
        {"osm_id": "node/3", "name": "District Hospital", "lat": 31.0, "lon": 71.0},  # another city
        {"osm_id": "node/4", "name": None, "lat": 30.0, "lon": 70.0},
    ])
    kept, dropped = standardise.deduplicate(df, radius_m=150)
    assert list(dropped["osm_id"]) == ["node/1"]  # the record with fewer fields goes
    assert set(kept["osm_id"]) == {"way/2", "node/3", "node/4"}


def test_district_assignment_with_snapping():
    districts = pd.DataFrame({"district": ["A", "B"], "district_id": ["a", "b"],
                              "geometry": [box(70, 30, 71, 31), box(71, 30, 72, 31)]})
    df = facilities([
        {"osm_id": "node/1", "name": "x", "lat": 30.5, "lon": 70.5},
        {"osm_id": "node/2", "name": "y", "lat": 30.5, "lon": 72.01},  # ~1 km outside B
        {"osm_id": "node/3", "name": "z", "lat": 35.0, "lon": 75.0},   # far away
    ])
    out = standardise.assign_districts(df, districts)
    assert list(out["district"]) == ["A", "B", None]
    assert list(out["district_match"]) == ["inside", "snapped", "outside"]


def test_graph_and_density_query():
    fac = facilities([
        {"osm_id": "node/1", "name": "H1", "lat": 30.5, "lon": 70.5, "district": "A", "specialties": ["cardiology"]},
        {"osm_id": "node/2", "name": "H2", "lat": 30.6, "lon": 70.6, "district": "A"},
        {"osm_id": "node/3", "name": "C1", "lat": 30.6, "lon": 71.6, "district": "B", "type": "clinic"},
    ])
    districts = pd.DataFrame({"district": ["A", "B"], "province": ["P", "P"], "population": [200000, 100000]})
    g = graph.build(fac, districts, [("who_doctors_per_10k", 2024, 12.8)])
    tables = graph.run_queries(g, "queries")
    density = tables["01_hospitals_per_100k"].set_index("district")
    assert density.loc["A", "hospitals"] == 2
    assert density.loc["A", "per_100k"] == 1.0
    assert list(tables["02_districts_without_hospital"]["district"]) == ["B"]
    spec = tables["03_specialties_by_province"]
    assert spec.iloc[0].to_dict() == {"province": "P", "specialty": "cardiology", "facilities": 1}
