"""Coverage tests for backend/overlay.py — the font-fallback path used when
no TrueType font is available (e.g. a slim container image missing
ttf-dejavu). Rendering must still degrade to PIL's built-in default font
rather than crash the poster overlay pipeline.
"""
import io
from PIL import Image


def _make_test_image(width: int = 200, height: int = 300) -> bytes:
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_overlay_poster_falls_back_to_default_font_when_all_truetype_paths_fail(monkeypatch):
    """Every font_paths candidate raising (e.g. file not found on this host)
    must not crash rendering — PIL's built-in default font is used instead.

    Only string-path calls (the font_paths candidates) are made to fail;
    PIL's own load_default() also calls truetype() internally (with an
    in-memory font blob, not a path) and must keep working so the fallback
    itself doesn't break.
    """
    from PIL import ImageFont

    real_truetype = ImageFont.truetype

    def _fail_only_for_path_candidates(font=None, *args, **kwargs):
        if isinstance(font, str):
            raise OSError("cannot open resource")
        return real_truetype(font, *args, **kwargs)

    monkeypatch.setattr(ImageFont, "truetype", _fail_only_for_path_candidates)

    from backend.overlay import _overlay_poster_sync
    result = _overlay_poster_sync(_make_test_image(), 3, "fr")

    assert result is not None
    img = Image.open(io.BytesIO(result))
    assert img.format == "JPEG"
