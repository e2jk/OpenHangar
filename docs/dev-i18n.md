# Developer Guide — Internationalisation (i18n) & Weblate

OpenHangar uses [Flask-Babel](https://python-babel.github.io/flask-babel/) for
internationalisation. [Weblate](https://weblate.org) is the recommended tool for
managing translations collaboratively without requiring translators to touch Git
directly.

---

## How it works

```
Source code  →  pybabel extract  →  messages.pot  →  Weblate
                                                         ↓
git ← pybabel update ← messages.po (per language) ← translator
                ↓
         pybabel compile (Docker build / CI)
                ↓
           messages.mo  (runtime, not committed)
```

Only `.po` files are committed to the repository. `.pot` and `.mo` are generated
automatically (gitignored).

---

## Setting up a Weblate component

1. **Host**: use [hosted.weblate.org](https://hosted.weblate.org/engage/openhangar/) (free for
   open-source) or a self-hosted instance.
2. **Create project** → **Add component** with these settings:

   | Field | Value |
   |---|---|
   | File format | `GNU Gettext PO file` |
   | Source file mask | `app/translations/*/LC_MESSAGES/messages.po` |
   | Monolingual base language | *(leave empty — bilingual PO)* |
   | Template file | *(leave empty)* |
   | Source language | English |
   | Version control | Git |
   | Repository | your GitHub URL |
   | Branch | `main` |

3. Enable **"Push on commit"** and configure a Weblate bot account with write
   access to the repo (or use GitHub's Weblate app integration for automatic PRs).

---

## Adding a new language

1. Add the locale code to `SUPPORTED_LOCALES` and `LOCALE_META` in `app/init.py`.
2. Extract fresh strings and initialise the new catalog (run from repo root):
   ```bash
   pybabel extract --no-wrap -F babel.cfg -o /tmp/messages.pot .
   pybabel init -i /tmp/messages.pot -d app/translations -l <lang>
   ```
3. Commit the new `app/translations/<lang>/LC_MESSAGES/messages.po`.
4. Add a flag emoji entry in `LOCALE_META` (`app/init.py`) and the native/English
   names so the navbar dropdown renders correctly.
5. Weblate will pick up the new component automatically on the next sync.

---

## Developer workflow (keeping translations up to date)

After wrapping new strings in `_()` in templates or route files:

```bash
# 1. Extract, update, and compile in one step
bash scripts/update_i18n.sh

# 2. Translate the new empty msgstr entries (or let Weblate do it)

# 3. Commit the updated .po files
git add app/translations/
git commit -m "i18n: update translation catalogs"
```

The script always runs from the repository root regardless of where it is
called from, so there is no risk of accidentally passing `app/` as the input
directory (which would silently drop all Jinja2 template strings). It also
passes `--no-wrap`, `--ignore-obsolete`, and `--ignore-pot-creation-date` so
the output is stable and idempotent between runs.

The CI pipeline (`ci.yml`) **hard-fails** if any locale has untranslated
strings — the build will not pass until every `msgstr` is filled in. A
pre-push hook can catch this locally before the push reaches CI; see
[development.md](development.md#git-hooks).

---

## Formatting: single-line (`--no-wrap`) is the project standard

`scripts/update_i18n.sh` always passes `--no-wrap`, so every entry it writes
is a single physical line, however long. Weblate's own PO writer does not —
it wraps at ~79 columns. Because both tools maintain the same `.po` files,
a locale that was last touched by a Weblate push and then updated locally
will show a **huge diff that's pure reformatting**, every unchanged entry
flipping from wrapped to single-line. That's expected, not a sign anything
went wrong — before assuming content was lost, diff on `msgid`/`msgstr`
pairs rather than raw lines, e.g.:

```python
import polib
before = polib.pofile("path/to/old.po")
after = polib.pofile("path/to/new.po")
# compare {e.msgid: e.msgstr for e in before} against the same for `after`
```

If you're adding strings by hand instead of running the script (the
"1–10 strings" shortcut in `AGENTS.md`), write the `msgstr` as a single
line too — matching the script's own output keeps the two workflows from
fighting over formatting on every subsequent run.

---

## Local compilation (outside Docker)

The compiled `.mo` files are generated at Docker image build time and are not
committed to the repository. For local development outside Docker:

```bash
pybabel compile -d app/translations
```

Run this after pulling changes that update `.po` files.

---

## Reviewing Weblate's quality-check flags

Weblate runs automated [quality checks](https://docs.weblate.org/en/latest/user/checks.html)
on every translated string (consistency, format-string mismatches, XML tag
mismatches, duplicated content, etc.). Some are false positives that are fine to
dismiss on Weblate (e.g. "Unchanged translation" when a word is genuinely
identical in both languages, like *Production*/*Production* in French); others
point at real problems worth fixing in the source strings or the `.po` files
directly.

`scripts/weblate_check_report.py` pulls every flagged string for each locale in
`SUPPORTED_LOCALES` (`app/init.py`, so new languages need no script change) —
including English: it's the source language, but Weblate still flags checks
against it (e.g. two distinct English strings both translated the same way
elsewhere shows up as a "Reused translation" flag on the English source unit
too, not just on its fr/nl translations). Writes a Markdown report grouped by
language and check type — source string, current translation, the check's own
note, source locations, and a direct Weblate edit link:

```bash
python3 scripts/weblate_check_report.py
# → writes weblate_checks_report.md (gitignored) at the repo root,
#   plus a weblate_checks_report.json cache next to it (used by --recheck)
```

Strings that share a translation but whose English source differs only by
capitalization (e.g. `Aircraft Type` / `Aircraft type`, both → *Vliegtuigtype*
in Dutch) get pulled into their own "Case-variant duplicates" section per
language instead of being buried in the generic "Reused translation" list —
that pattern is almost always the same string accidentally duplicated, worth
consolidating into one.

No Weblate login is required (the project is public), but an API token raises
the rate limit from 100 anonymous requests/day to 5000/hour — create one at
<https://hosted.weblate.org/accounts/profile/#api> if running this often.
Pass it via `WEBLATE_API_TOKEN` (env var), `--token`, or a
`WEBLATE_API_TOKEN=...` line in a `.env` file at the repo root — `.env` is
already gitignored, and the script reads it itself (no extra setup needed).
The check name/description is
scraped from each string's public translate page (the REST API only exposes a
`has_failing_check` boolean, not which check fired) — if Weblate changes that
page's markup, the string still gets reported, just without a parsed check
name.

The resulting report is plain Markdown — read it directly, or hand it to an AI
coding assistant to triage: which "Reused translation" flags indicate two
distinct English strings that should be factored into one, and which
duplicated/consistency flags point at an actual bug in a `.po` file (e.g. a
`msgstr` that was accidentally duplicated/concatenated with itself).

### Rechecking after a local fix, without re-querying Weblate

Once you've started fixing what the report found — merging a case-variant
duplicate, editing a `.po` file directly — those changes aren't on Weblate yet;
it only sees them after they're pushed and it syncs. Running the script again
at that point would just re-show the same stale results. Instead:

```bash
python3 scripts/weblate_check_report.py --recheck
```

This skips Weblate entirely and re-derives the report from the last cached run
(`weblate_checks_report.json`) by checking each previously-flagged string
against the *current* local files: the committed `.po` for fr/nl, or a fresh
`pybabel extract` for English (which has no `.po` file of its own — it's the
source language). Anything no longer present with the same content is dropped
as already fixed; the rest is reported exactly as before. Requires having run
the script at least once without `--recheck` first.

### Automated scan in CI (Code Scanning)

The same flagged strings also show up automatically in **Security → Code
Scanning**, under the `weblate-i18n` category: `.github/workflows/weblate-i18n-scan.yml`
runs daily (and on manual `workflow_dispatch`) using
[e2jk/weblate-checks-action](https://github.com/e2jk/weblate-checks-action)
(a self-contained, project-agnostic action — fetch-from-Weblate +
convert-to-SARIF — originally developed here, later split into its own
repository; see its README there). Format/markup checks
that mean a translation is actually malformed at render time (`Python
format`, `XML markup`, `Mismatching line breaks`, ...) show up as
`warning`; everything else (`Reused translation`, `Unchanged translation`,
...) as `note`, so they never outrank real security findings in the same
list. The scan is informational only and never fails the build. It needs a
`WEBLATE_API_TOKEN` repository secret to run reliably: this workflow's own
API usage is tiny (one request per language), but GitHub-hosted runners
share a rotating pool of egress IPs across unrelated CI jobs worldwide, and
Weblate's 100-requests/day anonymous quota is keyed by IP — so it can
already be spent by someone else's workflow before this one runs. See
`docs/backlog.md` for that setup step.
