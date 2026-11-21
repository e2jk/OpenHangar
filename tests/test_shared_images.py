"""Reusable photo cleaning keeps metadata and disk storage separate."""

import io
import re
from pathlib import Path

from flask import Flask
from PIL import Image
from services import images
from werkzeug.datastructures import FileStorage


def test_clean_photo_applies_orientation_and_removes_metadata(
    app: Flask, tmp_path: Path
) -> None:
    source = io.BytesIO()
    photo = Image.new("RGB", (8, 4), "blue")
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "PRIVATE DESCRIPTION"
    photo.save(
        source,
        format="JPEG",
        exif=exif,
        comment=b"PRIVATE COMMENT",
        icc_profile=b"PRIVATE PROFILE",
    )
    with app.app_context():
        data = images.clean_photo(
            FileStorage(stream=io.BytesIO(source.getvalue()), filename="trip.jpg")
        )
    assert not list(tmp_path.iterdir())
    with Image.open(io.BytesIO(data)) as cleaned:
        assert cleaned.format == "JPEG"
        assert cleaned.size == (4, 8)
        assert not cleaned.getexif()
        assert "comment" not in cleaned.info
        assert "icc_profile" not in cleaned.info
    folder = tmp_path / "places"
    filename = images.store(folder, data)
    assert re.fullmatch(r"[a-f0-9]{32}\.jpg", filename)
    assert (folder / filename).read_bytes() == data
