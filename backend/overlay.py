"""
Poster overlay rendering — 'Supprimé dans Xj' banner via Pillow.

Public API:
  _overlay_poster_sync   — CPU-bound PIL rendering (sync, for thread pool)
  _overlay_poster        — async wrapper (runs sync version in executor)
"""
import asyncio
import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _overlay_poster_sync(image_bytes: bytes, days_left: int, ui_lang: str = "fr") -> Optional[bytes]:
    """
    CPU-bound PIL rendering — synchronous so it can run in a thread pool.
    Called via run_in_executor from the async wrapper below.
    """
    if not image_bytes:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        w, h = img.size

        banner_h = max(52, int(h * 0.13))
        banner = Image.new("RGBA", (w, banner_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(banner)
        draw.rectangle([0, 0, w, banner_h], fill=(200, 30, 30, 230))

        if ui_lang == "en":
            label = f"Deleted in {days_left}d" if days_left > 0 else "Deletion today"
        else:
            label = f"Supprimé dans {days_left}j" if days_left > 0 else "Suppression aujourd'hui"

        # Font search order: env override, Debian/Ubuntu paths, Alpine Linux path,
        # then Liberation as fallback. Install ttf-dejavu in Dockerfile if missing.
        import os as _os
        _env_font = _os.environ.get("HYGIE_FONT_PATH", "")
        font_paths = tuple(filter(None, [
            _env_font,
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",   # Debian/Ubuntu
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",            # some Debian variants
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",               # Alpine (apk add ttf-dejavu)
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]))
        font = None
        max_font = banner_h - 10
        for size in range(max_font, 8, -1):
            for fp in font_paths:
                try:
                    candidate = ImageFont.truetype(fp, size)
                    bbox = draw.textbbox((0, 0), label, font=candidate)
                    if bbox[2] - bbox[0] <= w - 8 and bbox[3] - bbox[1] <= banner_h - 4:
                        font = candidate
                        break
                except Exception:
                    continue
            if font:
                break
        if font is None:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), label, font=font)
        x = max(0, (w - (bbox[2] - bbox[0])) // 2)
        y = max(0, (banner_h - (bbox[3] - bbox[1])) // 2)
        draw.text((x + 1, y + 1), label, font=font, fill=(0, 0, 0, 160))
        draw.text((x, y), label, font=font, fill=(255, 255, 255, 255))

        result = img.copy()
        result.paste(banner, (0, h - banner_h), banner)

        out = io.BytesIO()
        result.convert("RGB").save(out, format="JPEG", quality=88)
        logger.debug(f"Overlay: {w}x{h}, banner={banner_h}px, label={label!r}")
        return out.getvalue()
    except Exception as e:
        logger.warning(f"_overlay_poster_sync error: {e}")
        return None


async def _overlay_poster(image_bytes: bytes, days_left: int, ui_lang: str = "fr") -> Optional[bytes]:
    """Async wrapper — runs CPU-bound PIL work in a thread pool to avoid blocking the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _overlay_poster_sync, image_bytes, days_left, ui_lang)
