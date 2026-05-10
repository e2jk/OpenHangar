# Translation Management with Weblate

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

1. **Host**: use [hosted.weblate.org](https://hosted.weblate.org) (free for
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
2. Extract fresh strings and initialise the new catalog:
   ```bash
   pybabel extract -F babel.cfg -o /tmp/messages.pot .
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
# 1. Extract — generates a temporary .pot (gitignored)
pybabel extract -F babel.cfg -o /tmp/messages.pot .

# 2. Update existing .po files with new/removed msgids
pybabel update -i /tmp/messages.pot -d app/translations

# 3. Translate the new empty msgstr entries (or let Weblate do it)

# 4. Commit the updated .po files
git add app/translations/
git commit -m "i18n: update translation catalogs"
```

The CI pipeline (`ci.yml`) automatically checks for untranslated strings and
emits a warning annotation on the pull request if any are found, without
failing the build.

---

## Local compilation (outside Docker)

The compiled `.mo` files are generated at Docker image build time and are not
committed to the repository. For local development outside Docker:

```bash
pybabel compile -d app/translations
```

Run this after pulling changes that update `.po` files.
