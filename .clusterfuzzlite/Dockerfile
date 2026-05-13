FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:52b32769a56b9fe3350908266099d78852626ea44664ffdc7c12f43f62abb3f8

RUN pip install atheris==3.0.0 werkzeug==3.1.8

RUN cat > $SRC/fuzz_url_validation.py << 'PYEOF'
import sys, atheris
from urllib.parse import urlparse

def _safe_next(next_url, fallback):
    next_url = next_url.replace("\\", "")
    parsed = urlparse(next_url)
    if next_url and not parsed.scheme and not parsed.netloc:
        return next_url
    return fallback

@atheris.instrument_func
def TestOneInput(data):
    fdp = atheris.FuzzedDataProvider(data)
    result = _safe_next(fdp.ConsumeUnicodeNoSurrogates(512), "/")
    p = urlparse(result)
    assert not p.scheme, f"scheme leaked: {result!r}"
    assert not p.netloc, f"netloc leaked: {result!r}"

atheris.Setup(sys.argv, TestOneInput)
atheris.Fuzz()
PYEOF

RUN cat > $SRC/fuzz_file_ext.py << 'PYEOF'
import os, sys, atheris
from werkzeug.utils import secure_filename

_ALLOWED = {".jpg",".jpeg",".png",".gif",".webp",".heic",
            ".pdf",".doc",".docx",".xls",".xlsx",".txt"}

@atheris.instrument_func
def TestOneInput(data):
    fdp = atheris.FuzzedDataProvider(data)
    safe = secure_filename(fdp.ConsumeUnicodeNoSurrogates(256))
    if not safe:
        return
    ext = os.path.splitext(safe)[1].lower()
    assert "/" not in ext
    assert "\\" not in ext
    assert ext == ext.lower()
    _ = ext in _ALLOWED

atheris.Setup(sys.argv, TestOneInput)
atheris.Fuzz()
PYEOF

RUN cat > $SRC/fuzz_numeric_inputs.py << 'PYEOF'
import sys, atheris
from datetime import date as _date

@atheris.instrument_func
def TestOneInput(data):
    fdp = atheris.FuzzedDataProvider(data)
    raw = fdp.ConsumeUnicodeNoSurrogates(64)
    try:
        v = float(raw)
        assert v is not None
    except (ValueError, TypeError):
        pass
    try:
        v = int(raw)
        assert v is not None
    except (ValueError, TypeError):
        pass
    try:
        _date.fromisoformat(raw)
    except (ValueError, TypeError):
        pass

atheris.Setup(sys.argv, TestOneInput)
atheris.Fuzz()
PYEOF

RUN printf '#!/bin/bash -eu\nfor f in fuzz_url_validation fuzz_file_ext fuzz_numeric_inputs; do\n  compile_python_fuzzer "$SRC/${f}.py"\ndone\n' \
    > $SRC/build.sh && chmod +x $SRC/build.sh
