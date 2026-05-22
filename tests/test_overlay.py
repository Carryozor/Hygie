"""Unit tests for poster overlay rendering."""
import io
import pytest
from PIL import Image


def _make_test_image(width: int = 200, height: int = 300) -> bytes:
    """Generate a minimal valid JPEG for testing."""
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_overlay_poster_returns_valid_jpeg():
    from backend.scheduler import _overlay_poster_sync
    result = _overlay_poster_sync(_make_test_image(), 15, "fr")
    assert result is not None
    assert len(result) > 100
    img = Image.open(io.BytesIO(result))
    assert img.format == "JPEG"


def test_overlay_poster_preserves_dimensions():
    """Output image must have the same width as input (height may vary minimally)."""
    from backend.scheduler import _overlay_poster_sync
    original = _make_test_image(300, 450)
    result = _overlay_poster_sync(original, 10, "fr")
    assert result is not None
    out_img = Image.open(io.BytesIO(result))
    in_img = Image.open(io.BytesIO(original))
    assert out_img.size[0] == in_img.size[0]  # same width
    assert out_img.size[1] == in_img.size[1]  # same height


def test_overlay_poster_french_label_does_not_raise():
    from backend.scheduler import _overlay_poster_sync
    result = _overlay_poster_sync(_make_test_image(), 5, "fr")
    assert result is not None


def test_overlay_poster_english_label_does_not_raise():
    from backend.scheduler import _overlay_poster_sync
    result = _overlay_poster_sync(_make_test_image(), 5, "en")
    assert result is not None


def test_overlay_poster_zero_days_shows_imminent():
    """days_left=0 must render 'Imminent' without error."""
    from backend.scheduler import _overlay_poster_sync
    result = _overlay_poster_sync(_make_test_image(), 0, "fr")
    assert result is not None


def test_overlay_poster_invalid_bytes_returns_none():
    """Corrupt image bytes must be handled gracefully."""
    from backend.scheduler import _overlay_poster_sync
    result = _overlay_poster_sync(b"not-an-image", 10, "fr")
    assert result is None


def test_overlay_poster_empty_bytes_returns_none():
    from backend.scheduler import _overlay_poster_sync
    result = _overlay_poster_sync(b"", 10, "fr")
    assert result is None


async def test_overlay_poster_async_wrapper_non_blocking():
    """The async wrapper must complete without blocking the event loop."""
    import asyncio
    from backend.scheduler import _overlay_poster

    result = await _overlay_poster(_make_test_image(), 7, "fr")
    assert result is not None
    img = Image.open(io.BytesIO(result))
    assert img.format == "JPEG"
