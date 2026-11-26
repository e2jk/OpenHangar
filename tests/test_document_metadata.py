"""Image documents lose their EXIF/XMP/text metadata, without re-encoding."""

import io

from PIL import Image, PngImagePlugin
from services.images import strip_metadata  # pyright: ignore[reportMissingImports]


def _exif() -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # Make
    exif[0x8825] = {1: "N", 2: (50.0, 51.0, 0.0)}  # GPS
    return exif.tobytes()


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_jpeg_loses_exif_and_keeps_its_pixels(tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 10, 10)).save(buf, "JPEG", exif=_exif())
    path = _write(tmp_path, "receipt.jpg", buf.getvalue())
    before = Image.open(path).tobytes()
    strip_metadata(str(path))
    with Image.open(path) as img:
        assert not img.getexif()
        assert img.tobytes() == before


def test_png_loses_exif_and_text_chunks(tmp_path):
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "taken at home")
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "PNG", pnginfo=info, exif=_exif())
    path = _write(tmp_path, "scan.png", buf.getvalue())
    strip_metadata(str(path))
    data = path.read_bytes()
    assert b"eXIf" not in data and b"tEXt" not in data
    with Image.open(path) as img:
        assert img.size == (4, 4)


def test_fill_bytes_are_skipped(tmp_path):
    jpeg = b"\xff\xd8\xff\xff\xe1\x00\x04ab\xff\xda\x00\x02scan"
    path = _write(tmp_path, "fill.jpg", jpeg)
    strip_metadata(str(path))
    assert path.read_bytes() == b"\xff\xd8\xff\xda\x00\x02scan"


def test_other_or_broken_files_are_left_as_they_are(tmp_path):
    cases = {
        "doc.pdf": b"%PDF-1.7 whatever",
        "broken.jpg": b"\xff\xd8not a segment at all",
        "short.jpg": b"\xff\xd8\xff\xe0\x00\x04ab",
        "short.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\x01tEXtx",
    }
    for name, data in cases.items():
        path = _write(tmp_path, name, data)
        strip_metadata(str(path))
        assert path.read_bytes() == data, name
