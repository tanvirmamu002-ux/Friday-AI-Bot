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

TMP_DIR = "/tmp"
REQUEST_TIMEOUT = 15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _tmp_path(ext: str = "jpg") -> str:
    return os.path.join(TMP_DIR, f"{uuid.uuid4()}.{ext}")


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
    Try each image URL from results list.
    First attempt: send URL directly.
    Second attempt: download then send.
    Returns True if any image was sent successfully.
    """
    for item in image_results[:8]:
        url = item.get("image") or item.get("url", "")
        if not url:
            continue

        kwargs = dict(chat_id=chat_id, caption=caption[:1024])
        if reply_to:
            kwargs["reply_to_message_id"] = reply_to

        # Attempt 1 — direct URL
        try:
            bot.send_photo(photo=url, **kwargs)
            log.debug(f"Sent image via direct URL: {url[:60]}")
            return True
        except Exception as e:
            log.debug(f"Direct URL failed: {e}")

        # Attempt 2 — download first
        data = download_image(url)
        if data:
            try:
                bot.send_photo(photo=data, **kwargs)
                log.debug("Sent image via download")
                return True
            except Exception as e:
                log.debug(f"Download-send failed: {e}")

    return False


def enhance_image(input_path: str) -> str | None:
    """
    Enhance image quality using Pillow.
    Returns output path or None on failure.
    """
    if not PIL_AVAILABLE:
        return None

    output_path = _tmp_path("jpg")
    try:
        img = Image.open(input_path)

        # Convert to RGB
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        elif img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg

        # Resize if too small (upscale 2x for small images)
        w, h = img.size
        if w < 800 or h < 800:
            new_w, new_h = w * 2, h * 2
            img = img.resize((new_w, new_h), Image.LANCZOS)
            log.debug(f"Upscaled {w}x{h} → {new_w}x{new_h}")

        # Unsharp mask (sharpen)
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

        # Contrast
        img = ImageEnhance.Contrast(img).enhance(1.15)

        # Sharpness
        img = ImageEnhance.Sharpness(img).enhance(1.4)

        # Color saturation (slight boost)
        img = ImageEnhance.Color(img).enhance(1.1)

        # Brightness (very slight)
        img = ImageEnhance.Brightness(img).enhance(1.05)

        img.save(output_path, "JPEG", quality=92, optimize=True)
        log.info(f"Image enhanced → {output_path}")
        return output_path

    except Exception as e:
        log.error(f"Image enhance failed: {e}")
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
        log.error(f"Failed to save Telegram photo: {e}")
        return None


def cleanup(*paths):
    """Remove temp files silently."""
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
