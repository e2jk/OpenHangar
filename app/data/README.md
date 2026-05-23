# app/data

Static data files used by the application at runtime (not served to browsers).

## airports.csv

A filtered subset of the [OurAirports](https://ourairports.com/data/) public-domain
dataset, limited to airports with 4-letter ICAO identifiers.

The bundled file contains a curated set of airports for development and testing.

### Updating to the full dataset

To replace the bundled file with the complete ICAO airport database:

```bash
# Download the full OurAirports airports.csv (public domain)
curl -o /tmp/airports_full.csv https://davidmegginson.github.io/ourairports-data/airports.csv

# Filter to 4-letter ICAO codes only (removes heliports, seaplane bases, etc.)
python3 - <<'EOF'
import csv, re, sys
with open("/tmp/airports_full.csv", newline="", encoding="utf-8") as f_in, \
     open("airports.csv", "w", newline="", encoding="utf-8") as f_out:
    reader = csv.DictReader(f_in)
    writer = csv.DictWriter(f_out, fieldnames=reader.fieldnames)
    writer.writeheader()
    for row in reader:
        if re.match(r"^[A-Z]{4}$", row.get("ident", "").strip()):
            writer.writerow(row)
print("Done.")
EOF
```

The resulting file is approximately 2–3 MB and covers ~25 000 airports worldwide.
