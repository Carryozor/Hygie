"""Unit tests for poster overlay rendering."""
import io
from PIL import Image


def _make_test_image(width: int = 200, height: int = 300) -> bytes:
    """Generate a minimal valid JPEG for testing."""
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_overlay_poster_returns_valid_jpeg():
    from backend.overlay import _overlay_poster_sync
    result = _overlay_poster_sync(_make_test_image(), 15, "fr")
    assert result is not None
    assert len(result) > 100
    img = Image.open(io.BytesIO(result))
    assert img.format == "JPEG"


def test_overlay_poster_preserves_dimensions():
    """Output image must have the same width as input (height may vary minimally)."""
    from backend.overlay import _overlay_poster_sync
    original = _make_test_image(300, 450)
    result = _overlay_poster_sync(original, 10, "fr")
    assert result is not None
    out_img = Image.open(io.BytesIO(result))
    in_img = Image.open(io.BytesIO(original))
    assert out_img.size[0] == in_img.size[0]  # same width
    assert out_img.size[1] == in_img.size[1]  # same height


def test_overlay_poster_french_label_does_not_raise():
    from backend.overlay import _overlay_poster_sync
    result = _overlay_poster_sync(_make_test_image(), 5, "fr")
    assert result is not None


def test_overlay_poster_english_label_does_not_raise():
    from backend.overlay import _overlay_poster_sync
    result = _overlay_poster_sync(_make_test_image(), 5, "en")
    assert result is not None


def test_overlay_poster_zero_days_shows_imminent():
    """days_left=0 must render 'Imminent' without error."""
    from backend.overlay import _overlay_poster_sync
    result = _overlay_poster_sync(_make_test_image(), 0, "fr")
    assert result is not None


def test_overlay_poster_invalid_bytes_returns_none():
    """Corrupt image bytes must be handled gracefully."""
    from backend.overlay import _overlay_poster_sync
    result = _overlay_poster_sync(b"not-an-image", 10, "fr")
    assert result is None


def test_overlay_poster_empty_bytes_returns_none():
    from backend.overlay import _overlay_poster_sync
    result = _overlay_poster_sync(b"", 10, "fr")
    assert result is None


async def test_overlay_poster_async_wrapper_non_blocking():
    """The async wrapper must complete without blocking the event loop."""
    from backend.overlay import _overlay_poster

    result = await _overlay_poster(_make_test_image(), 7, "fr")
    assert result is not None
    img = Image.open(io.BytesIO(result))
    assert img.format == "JPEG"


def test_overlay_banner_is_opaque_so_rebanding_covers_previous_label():
    """A banner must fully cover what is under it: re-banding an already-banded
    poster (same height => same banner area) must not leave a ghost label.

    A saturated green background stands in for a strongly contrasting previous
    banner; with a translucent fill (alpha 230) the corner stays ~(180,52,25).
    """
    from backend.overlay import _overlay_poster_sync
    w, h = 200, 300
    for bg in ((255, 255, 255), (0, 255, 0)):
        buf = io.BytesIO()
        Image.new("RGB", (w, h), bg).save(buf, format="JPEG", quality=88)
        first = _overlay_poster_sync(buf.getvalue(), 15, "fr")
        second = _overlay_poster_sync(first, 3, "en")
        for out in (first, second):
            px = Image.open(io.BytesIO(out)).convert("RGB").getpixel((3, h - 3))
            assert all(abs(c - t) <= 12 for c, t in zip(px, (200, 30, 30))), (bg, px)
