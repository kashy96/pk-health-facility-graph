"""Build an RDF knowledge graph from the clean tables.

Vocabulary: schema.org classes and properties where they exist, plus a small
project namespace (`pkh:`) for the rest. Every facility keeps a
`prov:wasDerivedFrom` link to its OpenStreetMap object.
"""

from decimal import Decimal
from pathlib import Path

from rdflib import RDF, RDFS, XSD, Graph, Literal, Namespace, URIRef

SCHEMA = Namespace("https://schema.org/")
PROV = Namespace("http://www.w3.org/ns/prov#")
PKH = Namespace("https://github.com/kashy96/pk-health-facility-graph/vocab#")
BASE = Namespace("https://github.com/kashy96/pk-health-facility-graph/id/")

TYPE_CLASS = {
    "hospital": SCHEMA.Hospital,
    "clinic": SCHEMA.MedicalClinic,
    "doctors": SCHEMA.Physician,
    "dentist": SCHEMA.Dentist,
    "pharmacy": SCHEMA.Pharmacy,
    "laboratory": SCHEMA.DiagnosticLab,
    "other": SCHEMA.MedicalOrganization,
}


def slug(text):
    return "".join(c if c.isalnum() else "-" for c in str(text).lower()).strip("-")


def build(facilities, districts, country):
    g = Graph()
    for prefix, ns in (("schema", SCHEMA), ("prov", PROV), ("pkh", PKH), ("id", BASE)):
        g.bind(prefix, ns)

    pk = BASE["country/pakistan"]
    g.add((pk, RDF.type, SCHEMA.Country))
    g.add((pk, SCHEMA.name, Literal("Pakistan")))
    for indicator, year, value in country:
        obs = BASE[f"observation/{indicator}-{year}"]
        g.add((obs, RDF.type, PKH.Observation))
        g.add((obs, PKH.indicator, Literal(indicator)))
        g.add((obs, PKH.year, Literal(int(year), datatype=XSD.gYear)))
        g.add((obs, PKH.value, Literal(float(value), datatype=XSD.decimal)))
        g.add((obs, PKH.about, pk))

    provinces = {}
    for d in districts.itertuples(index=False):
        node = BASE[f"district/{slug(d.district)}"]
        g.add((node, RDF.type, SCHEMA.AdministrativeArea))
        g.add((node, PKH.level, Literal("district")))
        g.add((node, SCHEMA.name, Literal(d.district)))
        parent = pk
        if isinstance(d.province, str):
            parent = provinces.setdefault(d.province, BASE[f"province/{slug(d.province)}"])
        g.add((node, SCHEMA.containedInPlace, parent))
        if d.population == d.population:  # not NaN
            g.add((node, PKH.population, Literal(int(d.population), datatype=XSD.integer)))
            g.add((node, PKH.populationYear, Literal(2017, datatype=XSD.gYear)))
    for name, node in provinces.items():
        g.add((node, RDF.type, SCHEMA.AdministrativeArea))
        g.add((node, PKH.level, Literal("province")))
        g.add((node, SCHEMA.name, Literal(name)))
        g.add((node, SCHEMA.containedInPlace, pk))

    specialties = set()
    for f in facilities.itertuples(index=False):
        node = BASE[f"facility/{f.osm_id.replace('/', '-')}"]
        g.add((node, RDF.type, TYPE_CLASS[f.type]))
        g.add((node, PKH.facilityType, Literal(f.type)))
        if f.name:
            g.add((node, SCHEMA.name, Literal(f.name)))
        if f.name_ur:
            g.add((node, SCHEMA.name, Literal(f.name_ur, lang="ur")))
        g.add((node, SCHEMA.latitude, Literal(round(f.lat, 6), datatype=XSD.decimal)))
        g.add((node, SCHEMA.longitude, Literal(round(f.lon, 6), datatype=XSD.decimal)))
        if f.operator_type:
            g.add((node, PKH.operatorType, Literal(f.operator_type)))
        if f.district:
            g.add((node, SCHEMA.containedInPlace, BASE[f"district/{slug(f.district)}"]))
        for s in f.specialties:
            spec = BASE[f"specialty/{slug(s)}"]
            g.add((node, SCHEMA.medicalSpecialty, spec))
            specialties.add((spec, s))
        g.add((node, PROV.wasDerivedFrom, URIRef(f"https://www.openstreetmap.org/{f.osm_id}")))
    for spec, label in specialties:
        g.add((spec, RDF.type, PKH.Specialty))
        g.add((spec, RDFS.label, Literal(label.replace("_", " "))))
    return g


def _plain(value):
    """rdflib term -> plain Python value (Decimal becomes a rounded float)."""
    if value is None:
        return None
    v = value.toPython()
    if isinstance(v, Decimal):
        return round(float(v), 3)
    return v


def run_queries(g, query_dir):
    """Run every .rq file in query_dir; returns {name: DataFrame}."""
    import pandas as pd

    results = {}
    for path in sorted(Path(query_dir).glob("*.rq")):
        res = g.query(path.read_text(encoding="utf-8"))
        cols = [str(v) for v in res.vars]
        rows = [[_plain(v) for v in row] for row in res]
        results[path.stem] = pd.DataFrame(rows, columns=cols)
    return results
