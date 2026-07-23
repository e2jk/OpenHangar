"""Fuzz the Weight & Balance envelope ray-casting check (aircraft/routes.py).

_point_in_polygon() is a safety-relevant calculation (is the loaded CG/weight
within the aircraft's certified envelope?) fed by cfg.envelope_points, a
DB-stored JSON field with no enforced schema. Found a real bug locally before
this was pushed: a malformed point (wrong shape or non-numeric) raised
IndexError/ValueError instead of degrading gracefully — fixed to return
False (the conservative "envelope check unavailable" answer) instead of
crashing the W&B entry page with an unhandled 500.
"""

import sys
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

with atheris.instrument_imports(include=["aircraft.routes"]):
    from aircraft.routes import _point_in_polygon  # noqa: E402

_MAX_POINTS = 8


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    cg = fdp.ConsumeRegularFloat()
    weight = fdp.ConsumeRegularFloat()

    n_points = fdp.ConsumeIntInRange(0, _MAX_POINTS)
    points = []
    for _ in range(n_points):
        shape = fdp.ConsumeIntInRange(0, 3)
        if shape == 0:
            points.append([fdp.ConsumeRegularFloat(), fdp.ConsumeRegularFloat()])
        elif shape == 1:
            points.append([fdp.ConsumeRegularFloat()])  # missing coordinate
        elif shape == 2:
            points.append(fdp.ConsumeUnicodeNoSurrogates(8))  # non-numeric
        else:
            points.append(None)

    result = _point_in_polygon(cg, weight, points)
    assert isinstance(result, bool), f"unexpected return type: {result!r}"


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
