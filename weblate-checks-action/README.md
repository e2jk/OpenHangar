# Weblate Quality Checks → SARIF

A [GitHub Action](https://docs.github.com/en/actions/creating-actions/about-custom-actions)
that fetches strings flagged by [Weblate](https://weblate.org)'s translation
quality checks ("Reused translation", "Mismatching line breaks", "XML
markup", ...) and converts them into a [SARIF](https://sarif.readthedocs.io/)
file, so they show up in the **Security → Code Scanning** tab of any
GitHub repository — the same list where CodeQL and other scanners report.

## Why this exists

Weblate's public REST API only exposes a boolean `has_failing_check` per
translated string — it doesn't say *which* check fired or why; that detail
only exists on the string's own HTML "translate" page. Nothing on the
[GitHub Marketplace](https://github.com/marketplace?type=actions) converts
that into a Code Scanning–compatible format today; the closest match
(`WeblateOrg/locale_lint`) is an archived, purely local `.po`-file linter and
can't see Weblate's own cross-string checks (like "Reused translation",
which needs the whole project's translation memory).

## Status

This action currently lives inside the
[OpenHangar](https://github.com/e2jk/OpenHangar) repository, where it's
being proven out on OpenHangar's own translations before being split into
its own repository. Until that split happens, other projects can still
reference it from OpenHangar with the `owner/repo/path@ref` syntax GitHub
Actions supports for actions that live in a subdirectory:

```yaml
uses: e2jk/OpenHangar/weblate-checks-action@main
```

(Pin to a commit SHA instead of `main` for anything beyond experimentation —
see "A note on versioning" below.) Once split into its own repository, this
will become the simpler `uses: e2jk/weblate-checks-action@v1`.

## If you've never written a GitHub Action before

A GitHub Action like this one is just a packaged, reusable step for a
workflow. `action.yml` is its manifest (what inputs it takes, what it
outputs, what it runs); `weblate_checks_to_sarif.py` is the actual logic.
This one is a "composite" action — it just runs a Python script, no Docker
image or JavaScript runtime involved. You use it by adding a `uses:` step to
a `.github/workflows/*.yml` file in *your own* repository — you don't copy
any files from here into your project.

## Quick start

Add a workflow like this to your repository (e.g.
`.github/workflows/weblate-scan.yml`):

```yaml
name: Weblate translation quality scan

on:
  schedule:
    - cron: '0 3 * * *'   # daily at 03:00 UTC — pick any time that suits you
  workflow_dispatch: {}    # lets you trigger a run manually from the Actions tab

permissions: read-all

jobs:
  weblate-scan:
    runs-on: ubuntu-latest
    permissions:
      security-events: write   # required to upload SARIF to Code Scanning
    steps:
      - uses: actions/checkout@v5   # needed so upload-sarif can resolve file paths

      - name: Run Weblate quality checks
        id: weblate
        uses: e2jk/OpenHangar/weblate-checks-action@main
        continue-on-error: true   # a Weblate hiccup shouldn't turn this job red
        with:
          project: your-weblate-project-slug
          component: your-weblate-component-slug
          token: ${{ secrets.WEBLATE_API_TOKEN }}   # optional, see below

      - name: Upload results to GitHub Code Scanning
        if: always() && hashFiles(steps.weblate.outputs.sarif-file) != ''
        uses: github/codeql-action/upload-sarif@v4
        with:
          sarif_file: ${{ steps.weblate.outputs.sarif-file }}
          category: weblate-i18n
```

That's it — no separate script to vendor into your repo. GitHub tracks
alerts by `(ruleId, location)` within a `category`, so re-running this on a
schedule automatically marks previously-flagged strings "Fixed" once they
stop being flagged, with no extra bookkeeping.

### Which languages get scanned

By default (no `languages` input, as in the example above) the action asks
Weblate itself which languages are configured for the component and scans
all of them — including the source language, which already comes back from
that same query even though it has no `.po` file of its own.

Set `languages` explicitly (e.g. `languages: en,fr,nl`) only if your project
has its own authoritative list of supported languages that can differ from
what's configured in Weblate — for example, a locale added to Weblate ahead
of the application actually supporting it yet, or one your app has since
retired. In that case, derive the value dynamically from wherever your own
code defines that list rather than hardcoding it — e.g. OpenHangar's own
[`weblate-i18n-scan.yml`](https://github.com/e2jk/OpenHangar/blob/main/.github/workflows/weblate-i18n-scan.yml)
reads its `SUPPORTED_LOCALES` constant from `app/init.py` in a prior step
and passes it through as `languages: ${{ steps.locales.outputs.languages }}`,
so a language addition there needs no workflow change either.

### About the Weblate API token

Anonymous access to Weblate's **REST API** is rate-limited to 100
requests/day. This action only spends that quota on the initial per-language
query — one API request per language, or more only if a single language has
over 100 flagged strings (results are paginated at 100/page). Fetching each
flagged string's check details afterwards hits its public *translate page*,
not the API, so those requests don't count against the 100/day quota (the
`delay` input paces them out as politeness towards the Weblate server, not
because of any quota). In practice this means the API quota is rarely the
bottleneck — but it can still be, on a project with many languages, one with
components that individually have more than 100 flagged strings in a single
language, or a self-hosted instance with a stricter limit. A token also
removes any doubt: create one at
`https://<your-weblate-instance>/accounts/profile/#api` (5000 requests/hour)
and store it as a repository secret
(**Settings → Secrets and variables → Actions → New repository secret**),
then reference it as `secrets.WEBLATE_API_TOKEN` like the example above. The
action still works without one — it just logs a note and runs against the
lower, anonymous quota.

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `weblate-url` | no | `https://hosted.weblate.org` | Base URL of the Weblate instance. |
| `project` | **yes** | — | Weblate project slug. |
| `component` | **yes** | — | Weblate component slug. |
| `languages` | no | `''` (auto-discover) | Comma-separated language codes to check, e.g. `en,fr,nl`. Leave empty to auto-discover every language configured for the component from Weblate (includes the source language automatically). See "Which languages get scanned" above. |
| `token` | no | `''` | Weblate API token. See above. |
| `output` | no | `weblate-checks.sarif` | Path to write the SARIF file to. |
| `delay` | no | `0.3` | Seconds to sleep between per-string page fetches (politeness delay). |
| `warning-checks` | no | format/markup checks (see `action.yml`) | Comma-separated check names that map to SARIF `warning` instead of `note`. |
| `verbose` | no | `false` | Log each flagged string and its check name(s) as they're fetched. |

## Outputs

| Output | Description |
|---|---|
| `sarif-file` | Path to the generated SARIF file (same as the `output` input). |
| `flagged-count` | Number of distinct strings with at least one failing check. |

## Severity mapping

Every SARIF result gets a `level`, chosen so Weblate findings never outrank
real security findings (CodeQL, Bandit, ...) in the same Code Scanning list:

- **`warning`** — checks in `warning-checks` (default: format-placeholder and
  markup checks — `Python format`, `XML markup`, `Mismatching line breaks`,
  ...). These mean the translation is actually malformed and will misrender
  or crash string formatting at runtime.
- **`note`** (the lowest SARIF level) — everything else, e.g. `Reused
  translation`, `Unchanged translation`. These are content-quality hints,
  not render-time breakage.

No `security-severity` property is ever set — that property is reserved for
actual vulnerabilities and would misfile a translation nit as a security
finding.

## Running it locally / outside GitHub Actions

The script has no dependencies beyond the Python standard library:

```bash
python3 weblate_checks_to_sarif.py \
  --project your-project --component your-component \
  --output /tmp/weblate-checks.sarif
```

Omitting `--languages` (as above) auto-discovers the component's configured
languages from Weblate; pass `--languages en,fr,nl` to scan an explicit list
instead. Run `python3 weblate_checks_to_sarif.py --help` for the full flag
list — it mirrors the action's inputs one-for-one.

## Tests

```bash
pip install pytest
python3 -m pytest tests/ -q
```

Tests mock all network calls — no live Weblate instance is contacted.

## A note on versioning

Once this is split into its own repository, consumers should pin to a
release tag (`@v1`) or, for maximum supply-chain safety, a commit SHA — not
a branch — the same way this repo pins every third-party action it uses
(see any workflow under `.github/workflows/` in OpenHangar for the pattern:
`uses: owner/action@<full-sha> # vX.Y.Z`). Until a first release is tagged,
pin to a commit SHA of this repository rather than `@main`.

## License

MIT — see [`LICENSE`](LICENSE).
