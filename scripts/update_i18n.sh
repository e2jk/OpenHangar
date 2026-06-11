#!/usr/bin/env bash
# Update and compile all translation catalogs.
#
# Always run from the project root (this script enforces that).
# Running pybabel extract from the wrong directory silently skips Jinja2
# templates and drops their translations to the obsolete section.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

source .venv/bin/activate

echo "Extracting messages..."
pybabel extract --no-wrap -F babel.cfg -k _l -o app/translations/messages.pot .

echo "Updating catalogs..."
pybabel update --no-wrap --ignore-obsolete --ignore-pot-creation-date --no-fuzzy-matching -i app/translations/messages.pot -d app/translations

echo "Compiling catalogs..."
pybabel compile -d app/translations

echo "Done. Check for empty msgstr entries with:"
echo "  python -c \"import polib; [print(e.msgid) for lang in ['fr','nl'] for e in polib.pofile(f'app/translations/{lang}/LC_MESSAGES/messages.po').untranslated_entries()]\""
