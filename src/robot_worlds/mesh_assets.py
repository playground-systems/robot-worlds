"""Shared limits for immutable mesh appearance assets."""
from pathlib import Path

from PIL import Image

MAX_TEXTURE_BYTES = 16 * 1024 * 1024
MAX_TEXTURE_PIXELS = 4096 * 4096


def validate_texture(path: str | Path) -> None:
    path = Path(path)
    if path.suffix.lower() != ".png" or not path.is_file():
        raise ValueError("Texture must be an existing PNG file")
    if path.stat().st_size > MAX_TEXTURE_BYTES:
        raise ValueError("Texture exceeds 16 MiB")
    try:
        with Image.open(path) as image:
            if image.format != "PNG" or image.width * image.height > MAX_TEXTURE_PIXELS:
                raise ValueError("Texture must be PNG with at most 16 megapixels")
            image.verify()
    except Image.DecompressionBombError as exc:
        raise ValueError("Texture exceeds the decoded image budget") from exc
