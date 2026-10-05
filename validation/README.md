# Validation: are the "no hospital" districts real gaps?

The pipeline finds 17 districts with no hospital in OpenStreetMap. To test whether these are gaps in services or gaps in the map, each district was checked against the health-facility lists published by Pakistan's Expanded Programme on Immunization (EPI), which list government facilities by district:

- Balochistan: https://www.epi.gov.pk/wp-content/uploads/2023/04/EPI-Health-Facilities-Balochistan.pdf
- Sindh: https://www.epi.gov.pk/wp-content/uploads/2023/04/List-of-EPI-Centers-in-Sindh_March-2023.pdf
- Khyber Pakhtunkhwa: https://www.epi.gov.pk/wp-content/uploads/2021/09/List-of-EPI-Centres-Khyberpaktunkhwa.pdf

Method: text extracted from each PDF; rows for the district searched for "DHQ", "THQ", "Hospital" or "Civil"; each match read by hand. Results are in `zero_hospital_districts.csv`.

Result: 16 of 17 districts have at least one government hospital in the official lists. For Lakki Marwat the rows in the KP list are misaligned in the PDF, so the check was inconclusive rather than negative.

Limits: the EPI lists record immunisation sites, so they confirm that a hospital exists but are not a complete hospital census. Only districts with zero mapped hospitals were checked; districts with some hospitals mapped may also be under-counted.
