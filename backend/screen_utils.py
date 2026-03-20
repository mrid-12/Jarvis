import io
import logging
import pyautogui
from PIL import Image, ImageDraw
from screen_capture import screenshot_with_cursor

logger = logging.getLogger(__name__)

def capture_screen_with_grid(quality: int = 95) -> bytes:
    """
    Takes a screenshot and overlays a 0-1000 coordinate grid for the AI to use.
    """
    try:
        jpeg_bytes = screenshot_with_cursor(quality=quality)
        with Image.open(io.BytesIO(jpeg_bytes)) as img:
            draw = ImageDraw.Draw(img, 'RGBA')
            w, h = img.size
            
            # Grid intervals
            for i in range(100, 1000, 100):
                x = int(i / 1000.0 * w)
                y = int(i / 1000.0 * h)
                
                # Draw yellow lines
                draw.line([(x, 0), (x, h)], fill=(255, 255, 0, 150), width=1)
                draw.line([(0, y), (w, y)], fill=(255, 255, 0, 150), width=1)
                
                # Draw text labels
                draw.text((x + 2, 2), str(i), fill="yellow")
                draw.text((x + 2, h - 20), str(i), fill="yellow")
                draw.text((2, y + 2), str(i), fill="yellow")
                draw.text((w - 30, y + 2), str(i), fill="yellow")

            # Final tick marks
            for i in range(10, 1000, 10):
                x = int(i / 1000.0 * w)
                if i % 100 != 0:
                    draw.line([(x, h), (x, h - 5)], fill=(255, 255, 0, 200), width=1)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            return buf.getvalue()
    except Exception as e:
        logger.warning("Failed to capture screen with grid: %s", e)
        # Fallback to raw screenshot if grid fails
        return screenshot_with_cursor(quality=quality)
