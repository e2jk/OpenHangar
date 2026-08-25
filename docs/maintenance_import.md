# Aircraft Maintenance Programme (AMP) import & export

OpenHangar can import a structured Aircraft Maintenance Programme (AMP) task
list — built externally, e.g. by comparing your aircraft's programme against
a sister aircraft's and your maintenance shop's tracking export — as the
aircraft's tracked maintenance schedule, instead of re-entering every
recurring item by hand. It can also export that schedule back out as a
document matching the official **EASA Form AMP** layout referenced by
[AMC2 ML.A.302](https://www.easa.europa.eu/en/downloads/138696/en) ("AMC & GM
to Part-ML", Annex V to ED Decision 2023/013/R) — the standard template used
across Part-ML AMPs regardless of maintenance shop.

This is a generic, reusable feature: the import template below is a fixed
column layout any owner can build a compatible workbook against, not a
one-off script tied to a specific aircraft or shop.

**Round-trip design:** import and export read and write the exact same
`MaintenanceTrigger` fields (`category`, `is_alternative_to_ica`, `reference`,
`action`, `part_number`, `serial_number`, the due/interval fields) and the
same `AmpDeclaration` profile — there is no separate "export data" kept in
sync by hand. Editing a maintenance item in OpenHangar after importing it
changes what the next export produces; there's nothing import-specific left
behind to go stale.

---

## 1. The AMP task list spreadsheet

Upload any `.xlsx` workbook containing a sheet with a header row that
includes at least a **Task description** and an **Interval** column.
Column order doesn't matter, other columns are optional, and any other
sheets or leading rows in the workbook (a README sheet, a comparison sheet,
notes above the header) are ignored — OpenHangar scans the first 30 rows of
every sheet for a header row matching the columns below.

| Column | Required | Maps to | Notes |
|---|---|---|---|
| `Category` | optional | `category` | See [AMP categories](#2-amp-categories) below. Blank or unrecognised text imports fine — the item just isn't itemised in Appendix B/block 4 on export. |
| `Task description` | **yes** | `name` | The item's title. |
| `Reference` | optional | `reference` | AD/SB/manual document reference. |
| `Action` | optional | `action` | Free text, e.g. `INSPECTION`, `REPLACE`, `TBO`, `SLL` — not a fixed list, since maintenance shops aren't consistent about these. |
| `Interval` | **yes** | `interval_hours`/`interval_days` | See [Interval syntax](#3-interval-syntax) below. |
| `Part number` | optional | `part_number` | Free text. |
| `Serial number` | optional | `serial_number` | Free text. |
| `Notes` | optional | `notes` | Free text. |

A row with no `Task description` is treated as a blank/trailing row and
silently skipped — it isn't counted and doesn't produce an error.

### Component association

Each row is optionally scoped to one of the aircraft's installed components
(engine, propeller, …) rather than the airframe in general — this drives
which running-hours total (`engine` vs `flight` hours) the item's hours
interval is measured against, and groups the item under that component on
the maintenance dashboard. There's no dedicated spreadsheet column for this:
OpenHangar suggests a component automatically when the `Category` or
`Task description` text mentions "engine" or "propeller" and the aircraft
has a matching installed component — you can accept or change the suggestion
per row on the review screen before anything is saved.

---

## 2. AMP categories

EASA Form AMP block 4 and Appendix B itemise **9 fixed categories** of
"additional maintenance requirements" beyond straight compliance with the
DAH's manuals. `Category` values are matched against this list
case/whitespace-tolerantly — an exact match (ignoring case and extra
whitespace) categorises the row; anything else (blank, "Routine DAH ICA
inspection", "Admin", a typo, …) leaves the row uncategorised, which is
correct for routine manual-driven inspections and admin items that the
official form doesn't itemise at all:

1. Maintenance due to specific equipment and modifications
2. Maintenance due to repairs
3. Maintenance due to life-limited components
4. Maintenance due to mandatory continuing airworthiness information (ALIs, CMRs, TCDS)
5. Maintenance recommendations (TBO via SB/SL, non-mandatory)
6. Maintenance due to repetitive ADs
7. Maintenance due to specific operational/airspace directives/requirements
8. Maintenance due to type of operation or operational approvals
9. Other

A category with at least one imported/entered item shows "Yes" in block 4 on
export, and gets its own section in Appendix B — both computed automatically
from whichever triggers currently carry that category, not stored
separately.

---

## 3. Interval syntax

The `Interval` column combines a flight-hour figure and/or a calendar figure
in one string, since many AMP items are due at "whichever comes first":

- `<n>FH` — a flight-hour interval, e.g. `100FH`
- `<n>DY` / `<n>MO` / `<n>YR` — a calendar interval in days, months, or
  years, e.g. `30DY`, `12MO`, `3YR` (months and years are converted to days
  at 30 and 365 days respectively)
- Combine both with a `/`, e.g. `100FH / 12MO` — the item is then due at
  whichever of the two is reached first, exactly like the official form's
  own combined intervals

An empty cell, the literal text `PENDING`, or any other text that doesn't
match the syntax above **never causes an import error and is never silently
skipped** — the row imports as a real maintenance item flagged **needs
review**, with no due date/hours set yet, so it's visible on the dashboard
as "not yet scheduled" rather than looking permanently fine. This is meant
for exactly the situation where part of your task list is still waiting on
shop input when you're ready to import the rest.

On export, a combined interval is rendered back into the same
`"100FH / 12MO"`-style text for Appendix B — the inverse of the parsing
above.

---

## 4. Importing

1. From an aircraft's **Maintenance** page, click **Import from
   spreadsheet**.
2. Upload your `.xlsx` file.
3. Review every parsed row: its computed category, interval, initial due
   date/hours, and suggested component. Adjust the component picker on any
   row that needs it — nothing is saved yet.
4. Click **Confirm import**. Every row becomes a maintenance item, tagged
   with this import as a batch; the summary shows how many were imported and
   how many were flagged for review.

Every import is recorded on the **Import history** page (linked from the
upload page). If you imported the wrong file, roll it back to remove every
item that batch created in one operation — items you entered by hand, or
that came from a different import, are never affected.

---

## 5. The AMP declaration profile

Blocks 1–3 and 6–9 of the official form cover information that isn't part
of the task list itself — the programme's basis (DAH ICA vs. a minimum
inspection programme), DAH ICA document references, the pilot-owner
maintenance declaration, who's declaring/certifying the programme and their
contact details, and revision history. Fill these in once from an
aircraft's **AMP declaration** page (linked from the Maintenance page) — a
normal form, not a spreadsheet import, since it's around 15 rarely-changed
fields rather than a per-item task list.

---

## 6. Exporting

Once the AMP declaration profile is filled in, **Export AMP** on the
Maintenance page produces a print-ready HTML document matching the official
form's block/appendix structure — blocks 1–9, plus Appendix B (grouped by
category), Appendix C (any items flagged as alternative to the DAH's ICA),
and Appendix D (free-text notes plus the revision record). Use the page's
**Print / download as PDF** button to save or print it; OpenHangar doesn't
generate PDF files directly, so this is the same "print to PDF" your browser
offers for any other page. Signature lines are left blank — OpenHangar
doesn't perform the legal act of signing the declaration.

Because export reads the same fields import writes, editing a maintenance
item's category, reference, or interval in OpenHangar — whether it came
from an import or was entered by hand — changes what the next export
produces. There's no separate export snapshot to regenerate or keep in sync.
