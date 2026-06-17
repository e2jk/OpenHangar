#!/usr/bin/env python3
"""
Verify all .po catalogs are complete: no untranslated or fuzzy entries.

Simulates what update_i18n.sh does (extract + update with identical flags)
on a temporary copy of each .po file, so the working tree is never modified.
Called by both the pre-push hook and CI to guarantee identical behaviour.

Exit 0 — all catalogs are clean.
Exit 1 — untranslated or fuzzy entries found; details printed to stdout.
"""

import pathlib
import shutil
import subprocess
import sys
import tempfile

import polib  # type: ignore[import-untyped]

REPO = pathlib.Path(__file__).resolve().parent.parent
PYBABEL = str(REPO / ".venv/bin/pybabel")
TRANSLATIONS = REPO / "app/translations"
BABEL_CFG = str(REPO / "babel.cfg")


def main() -> int:
    with tempfile.NamedTemporaryFile(suffix=".pot", delete=False) as f:
        pot = pathlib.Path(f.name)

    try:
        subprocess.run(
            [
                PYBABEL,
                "extract",
                "--no-wrap",
                "-F",
                BABEL_CFG,
                "-k",
                "_l",
                "-o",
                str(pot),
                str(REPO),
            ],
            check=True,
            capture_output=True,
        )

        fail = False
        for lang_dir in sorted(TRANSLATIONS.iterdir()):
            if not lang_dir.is_dir():
                continue
            lang = lang_dir.name
            po_src = lang_dir / "LC_MESSAGES/messages.po"
            if not po_src.exists():
                continue

            with tempfile.TemporaryDirectory() as tmp:
                po_tmp = pathlib.Path(tmp, lang, "LC_MESSAGES", "messages.po")
                po_tmp.parent.mkdir(parents=True)
                shutil.copy(po_src, po_tmp)
                subprocess.run(
                    [
                        PYBABEL,
                        "update",
                        "--no-wrap",
                        "--ignore-obsolete",
                        "--ignore-pot-creation-date",
                        "--no-fuzzy-matching",
                        "-i",
                        str(pot),
                        "-d",
                        tmp,
                        "-l",
                        lang,
                    ],
                    check=True,
                    capture_output=True,
                )
                po_obj = polib.pofile(str(po_tmp))
                untranslated = po_obj.untranslated_entries()
                fuzzy = po_obj.fuzzy_entries()

            if untranslated:
                print(
                    f"[translations] ERROR: {len(untranslated)} untranslated {lang} string(s)"
                    " — translate and commit messages.po before pushing."
                )
                for e in untranslated[:10]:
                    print(f"  {e.msgid!r}")
                fail = True

            if fuzzy:
                print(
                    f"[translations] ERROR: {len(fuzzy)} fuzzy {lang} string(s)"
                    " — review, translate, and remove #, fuzzy markers before pushing."
                )
                for e in fuzzy[:10]:
                    print(f"  {e.msgid!r}")
                fail = True

        if fail:
            return 1
        print("[translations] OK — no untranslated or fuzzy entries.")
        return 0

    finally:
        pot.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
