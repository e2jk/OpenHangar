"""Fuzz the Syncthing-mounted-path parser (documents/routes.py).

_parse_reconcile_filename interprets a category segment and a
"YYYY-MM-DD - title.ext" filename segment out of a locally-mounted
Syncthing folder tree, shared by scan_documents and
rename_reconcile_folder's inline rescan. The path segments come from
os.walk() over a directory the tenant/owner controls (not an
internet-facing input), so this is a lower-value target than the other
harnesses in this directory — but the regex + date parsing is still
hand-rolled, and a single unparseable filename must never abort the whole
folder scan.
"""

import sys
from datetime import date
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

with atheris.instrument_imports(include=["documents.routes"]):
    from documents.routes import _parse_reconcile_filename


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    cat_str = fdp.ConsumeUnicodeNoSurrogates(32)
    name_part = fdp.ConsumeUnicodeNoSurrogates(128)

    category, title_hint, date_hint = _parse_reconcile_filename(cat_str, name_part)

    assert category is None or isinstance(category, str), f"category: {category!r}"
    assert title_hint is None or isinstance(title_hint, str), (
        f"title_hint: {title_hint!r}"
    )
    assert date_hint is None or isinstance(date_hint, date), f"date_hint: {date_hint!r}"


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
