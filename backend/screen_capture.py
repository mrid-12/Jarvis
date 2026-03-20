"""
Windows-native screen capture with cursor overlay.

Uses ctypes + PIL ImageGrab for a full-resolution screenshot,
then draws the actual cursor position as a visible crosshair marker.
"""

import ctypes
from ctypes import wintypes
import io
from PIL import Image, ImageDraw, ImageGrab


def _get_cursor_pos() -> tuple[int, int] | None:
    """Return (x, y) cursor position in screen pixels, or None on failure."""
    try:
        pt = wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return pt.x, pt.y
    except Exception:
        pass
    return None


def screenshot_with_cursor(quality: int = 65) -> bytes:
    """
    Capture the full screen (including the cursor rendered as a red crosshair)
    and return JPEG bytes at the given quality.
    """
    # ImageGrab.grab() uses Windows PrintWindow — same coordinate space as mouse APIs
    img = ImageGrab.grab(all_screens=False)

    # Draw cursor position as a visible marker
    pos = _get_cursor_pos()
    if pos:
        cx, cy = pos
        draw = ImageDraw.Draw(img)
        r = 8
        # Red crosshair
        draw.line([(cx - r*2, cy), (cx + r*2, cy)], fill="red", width=2)
        draw.line([(cx, cy - r*2), (cx, cy + r*2)], fill="red", width=2)
        # Circle
        draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], outline="red", width=2)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def screenshot_bytes(quality: int = 65) -> bytes:
    """Alias for screenshot_with_cursor."""
    return screenshot_with_cursor(quality=quality)
