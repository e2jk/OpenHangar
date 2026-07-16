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
