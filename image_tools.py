"""
image_tools.py — Telegram photo handling, image enhance, safe send
"""

import os
import uuid
import logging
import requests
from io import BytesIO

log = logging.getLogger(__name__)

try:
    from PIL import Image, ImageFilter, ImageEnhance
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    log.warning("Pillow not available — image enhance disabled")

TMP_DIR         = "/tmp"
REQUEST_TIMEOUT = 15
USER_AGENT      = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF"}


def _tmp_path(ext: str = "jpg") -> str:
    return os.path.join(TMP_DIR, f"fri_{uuid.uuid4().hex[:8]}.{ext}")


def download_image(url: str) -> BytesIO | None:
    """Download image from URL, return BytesIO or None on failure."""
    try:
        resp = requests.get(
            url, timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            stream=True
        )
        if resp.status_code != 200:
            return None
        ctype = resp.headers.get("content-type", "")
        if not ctype.startswith("image/"):
            return None
        data = BytesIO(resp.content)
        data.name = "image.jpg"
        return data
    except Exception as e:
        log.debug(f"Image download failed ({url[:60]}): {e}")
        return None


def send_photo_safe(bot, chat_id: int, image_results: list[dict],
                    caption: str = "", reply_to: int | None = None) -> bool:
    """
    Try each image URL in order.
    Attempt 1: send URL directly.
    Attempt 2: download, then send.
    Returns True when any image is sent successfully.
    """
    kwargs = {"chat_id": chat_id, "caption": caption[:1024]}
    if reply_to:
        kwargs["reply_to_message_id"] = reply_to

    for item in image_results[:8]:
        url = item.get("image") or item.get("url", "")
        if not url:
            continue

        # Direct URL
        try:
            bot.send_photo(photo=url, **kwargs)
            return True
        except Exception:
            pass

        # Download-first fallback
        data = download_image(url)
        if data:
            try:
                bot.send_photo(photo=data, **kwargs)
                return True
            except Exception:
                pass

    return False


def enhance_image(input_path: str) -> str | None:
    """
    Enhance image using Pillow: sharpen, contrast, upscale.
    Returns output path or None on failure.
    """
    if not PIL_AVAILABLE:
        return None

    output_path = _tmp_path("jpg")
    try:
        img = Image.open(input_path)

        if img.format and img.format.upper() not in SUPPORTED_FORMATS:
            log.warning(f"Unsupported format: {img.format}")
            return None

        # Normalise to RGB (handle RGBA, P, L, etc.)
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Upscale small images
        w, h = img.size
        if w < 800 or h < 800:
            img = img.resize((w * 2, h * 2), Image.LANCZOS)
            log.debug(f"Upscaled {w}×{h} → {w*2}×{h*2}")

        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
        img = ImageEnhance.Contrast(img).enhance(1.15)
        img = ImageEnhance.Sharpness(img).enhance(1.4)
        img = ImageEnhance.Color(img).enhance(1.1)
        img = ImageEnhance.Brightness(img).enhance(1.05)

        img.save(output_path, "JPEG", quality=92, optimize=True)
        log.info(f"Enhanced → {output_path}")
        return output_path

    except Exception as e:
        log.error(f"enhance_image failed: {e}")
        return None


def save_telegram_photo(bot, photo_list) -> str | None:
    """Download highest-res Telegram photo to a temp file. Returns path or None."""
    try:
        file_info  = bot.get_file(photo_list[-1].file_id)
        downloaded = bot.download_file(file_info.file_path)
        path = _tmp_path("jpg")
        with open(path, "wb") as f:
            f.write(downloaded)
        return path
    except Exception as e:
        log.error(f"save_telegram_photo failed: {e}")
        return None


def cleanup(*paths):
    """Remove temp files silently."""
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
