# Pakistan health facility graph: integrating open data on where care is

**Muhammad Kashif** · [Portfolio](https://kashif-ai-portfolio.netlify.app) · [LinkedIn](https://www.linkedin.com/in/muhammad-kashif-928655142)

## Motivation

At work I build systems that match medical professionals with hospitals. Doing that for Pakistan would start with a basic question that has no easy answer: *where are the health facilities, and which districts have too few?* The information exists, but it is spread across sources that were never designed to fit together: volunteer-mapped facilities, administrative boundaries, a census table and national workforce statistics. Each has its own format, naming conventions and year.

This project builds a reproducible pipeline that integrates those sources into one knowledge graph, measures the quality of the result at every step, and uses the graph to ask where mapped coverage is thinnest.

## Research questions

- **RQ1.** How complete and consistent is open data on Pakistan's health facilities, field by field?
- **RQ2.** What does it take to integrate sources with different formats, vintages and naming conventions, and how much of that can be automated safely?
- **RQ3.** Where is mapped hospital density per person lowest, and how much of that pattern reflects missing services versus missing data?

## Sources

| Source | Format | What it provides | Licence |
|---|---|---|---|
| OpenStreetMap health facilities, via the [HOT export on HDX](https://data.humdata.org/dataset/hotosm_pak_health_facilities) | Zipped GeoJSON (points and building outlines) | Facility locations, names, types, specialties | ODbL |
| [geoBoundaries](https://www.geoboundaries.org/) PAK ADM2 (2019) | GeoJSON | 126 district-level boundaries | gbOpen |
| [HDX COD-PS Pakistan](https://data.humdata.org/dataset/cod-ps-pak) (2017 census) | CSV | District population and province | as listed on HDX |
| [WHO Global Health Observatory](https://www.who.int/data/gho) | JSON (OData API) | Doctors and nurses per 10,000 people, national | WHO terms |

Every downloaded file is stored unchanged in `data/raw/` with its URL, download time and SHA-256 in `data/raw/manifest.json`.

## Pipeline

```
download ──► standardise ──► deduplicate ──► place in districts ──► join population ──► quality checks ──► RDF graph ──► SPARQL ──► report
```

1. **Standardise.** Building outlines are reduced to a representative point. OSM `amenity`/`healthcare` tags map to seven facility types. Free-text specialty tags are split, lower-cased and normalised: *pediatrics* becomes *paediatrics*, *gynecology* becomes *gynaecology*, and so on.
2. **Deduplicate.** The same facility is often mapped twice, as a point and as a building. Records with the same normalised name within 150 m are merged, keeping the more complete one. Dropped records are kept for audit.
3. **Place in districts.** Point-in-polygon with an R-tree (`shapely.STRtree`). Points up to 5 km outside any boundary (coastline and border simplification) snap to the nearest district; the rest are flagged.
4. **Join population.** Boundary names (2019) and census names (2017) differ. Matching happens in two passes:
   - First pass: documented exclusions, manual aliases, and exact matches on a normalised key.
   - Second pass: fuzzy matching, strictly one-to-one, so two districts can never share one census row.

   Every decision and its method is written to `results/tables/district_name_matches.csv`.
5. **Quality checks.** Uniqueness, coordinate validity, field completeness, type coverage, district assignment and join coverage. Hard errors stop the pipeline; weaknesses of the open data itself are reported as warnings, because measuring them is part of the point.
6. **Knowledge graph.** RDF built with `rdflib`, using schema.org where possible:
   - facilities are `schema:Hospital`, `schema:MedicalClinic`, … and are linked to their district by `schema:containedInPlace`;
   - districts are linked to provinces the same way, and provinces to the country;
   - specialties are linked with `schema:medicalSpecialty`;
   - every facility has `prov:wasDerivedFrom` its OpenStreetMap object.
7. **SPARQL.** The analysis queries in `queries/` run against the graph, so the numbers come from the integrated data rather than from ad-hoc scripts.

## Results

From the snapshot in `data/raw/manifest.json` (HOT export last updated May 2026). Full output: [`results/RESULTS.md`](results/RESULTS.md).

**Integration**
- 5,076 raw facility records; 137 duplicates removed; **4,939** facilities in the graph.
- 115 of 126 district boundaries matched to census population:
  - 105 exact matches;
  - 7 fuzzy matches, all checked by hand (e.g. *Sheikhpura* / *Sheikhupura*, *Qilla Abdullah* / *Killa Abdullah*);
  - 3 manual aliases (*Karachi* → *Karachi City*, *Nawabshah* → *Shaheed Benazirabad*, *Vihari* → *Vehari*).
- The remaining 11 boundary units (Gilgit-Baltistan and Azad Jammu & Kashmir) have no row in the 2017 census file and are excluded by design.
- An early version used a looser fuzzy threshold and silently matched *Kharmang* (Gilgit-Baltistan) to *Kharan* (Balochistan), giving both the same population. The one-to-one rule and a stricter threshold prevent this; there is a regression test for it.
- The graph has about 36,000 triples.

**RQ1: completeness** (`results/figures/completeness.png`)

| Field | Filled |
|---|---|
| Facility type recognised | 96% |
| Name | 95% |
| City | 60% |
| Operator type (public/private) | 5% |
| Specialty | 5% |
| Urdu name | 4% |

Names and types are usable. Specialties and ownership are almost absent, so the open data cannot answer "where is the nearest cardiologist?" or "public or private?" at national scale.

**RQ3: mapped hospital density** (`results/figures/hospitals_per_100k.png`)
- Density ranges from **0** to **5.3 hospitals per 100,000 people** (Karachi 5.3, Islamabad 5.2).
- **17 districts have no hospital in OpenStreetMap at all.** They include Dadu (1.55 million people), Bannu and Kashmore. Most of the rest are in Balochistan.
- For context, WHO reports 12.8 doctors and 5.2 nurses and midwives per 10,000 people in Pakistan (2024).

**Validation: are the zeros real?** I checked all 17 zero-hospital districts against the government's EPI (immunisation programme) facility lists for Balochistan, Sindh and Khyber Pakhtunkhwa ([`validation/`](validation/)).

**16 of 17 have at least one government hospital**, e.g.:
- Civil Hospital Dadu;
- DHQ Hospital Batkhela (Malakand);
- DHQ hospitals in all ten Balochistan districts.

For Lakki Marwat the check was inconclusive, because that district's rows are misaligned in the source PDF.

So the zeros are gaps in the *map*, not in *services*, and the density map is best read as a map of where open data is weakest. That matters in its own right: any tool built on this data (routing, planning, matching) would be blindest exactly where it is most needed.

**Detail varies by province, too** (query `06_specialty_coverage_by_province`):

| Province | Mapped facilities | With a specialty recorded |
|---|---|---|
| Balochistan | 83 | 16.9% |
| Islamabad (Federal Capital Territory) | 236 | 8.1% |
| Punjab | 1,305 | 6.7% |
| Khyber Pakhtunkhwa | 343 | 6.7% |
| Sindh | 2,768 | 3.6% |

The province with by far the most mapped facilities records the least detail. One possible explanation is that many facilities were added as names and locations only, without follow-up tagging; checking the OSM edit history would test this.

## Limitations

- OpenStreetMap is volunteer-mapped; coverage follows mapper activity, not population.
- Population is from the 2017 census and boundaries from 2019. Districts created or split in between cannot be matched exactly.
- "Hospital" is whatever mappers tagged as a hospital. Size, beds and services are not recorded.
- The deduplication rule (same name within 150 m) misses duplicates with different spellings and cannot merge Urdu and English names.

## Next steps

- **Validation at scale:** the check above covers only the 17 zero-hospital districts. Matching every OSM hospital against the official lists would estimate OSM recall for every district.
- **Specialty inference:** infer likely specialties from facility names (e.g. *"Eye Hospital"*, *"Children's Hospital"*) and measure precision on the 5% of facilities that do have a specialty tag.
- **Accessibility:** travel time to the nearest hospital from population grids, instead of counts per district.
- **Streaming updates:** consume OpenStreetMap change files through a message queue (e.g. Kafka), so the graph updates continuously and quality metrics are tracked over time.

## How to run

```bash
python -m venv .venv
.venv\Scripts\activate            # on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python -m pytest                  # offline tests
python -m pkhealth.pipeline       # downloads sources on the first run (~2 MB), then about 20 s
python -m pkhealth.pipeline --refresh   # fetch the latest versions of all sources
```

Outputs, all in `results/`:
- `RESULTS.md`
- `quality_report.csv`
- `graph.ttl`
- `summary.json`
- `tables/`, including the clean facility table and every name-matching decision
- `figures/`

To explore the graph yourself, load `results/graph.ttl` into any SPARQL tool (e.g. Apache Jena Fuseki or GraphDB Free), or add a `.rq` file to `queries/`.

## Project layout

```
pkhealth/
  sources.py      downloads with provenance manifest
  standardise.py  facility schema, specialty normalisation, dedup, districts, name matching
  quality.py      data-quality checks
  graph.py        RDF graph and SPARQL runner
  pipeline.py     end-to-end run, report and figures
queries/          SPARQL analysis queries
validation/       check of the zero-hospital districts against official EPI facility lists
tests/            offline tests on small hand-made data
```

## Licence

Code: MIT. Data remains under the licences of its sources (above). OpenStreetMap data © OpenStreetMap contributors, ODbL.
