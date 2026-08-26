from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

import numpy as np
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

BITMAP_SIZE = 16
MATCH_THRESHOLD = 0.55


def _atlas_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "sourcehan_gb_bitmaps.npz"


@lru_cache(maxsize=1)
def load_atlas() -> tuple[list[str], np.ndarray]:
    payload = np.load(_atlas_path(), allow_pickle=True)
    chars = [str(item) for item in payload["chars"].tolist()]
    packed = payload["bits"]
    bits = np.unpackbits(packed, axis=1)[:, : BITMAP_SIZE * BITMAP_SIZE]
    return chars, bits


def glyph_bitmap(font: ImageFont.FreeTypeFont, character: str, size: int = BITMAP_SIZE) -> np.ndarray:
    image = Image.new("L", (72, 72), 255)
    ImageDraw.Draw(image).text((8, 4), character, font=font, fill=0)
    array = np.array(image)
    ink = array < 200
    if not ink.any():
        return np.zeros((size, size), dtype=np.uint8)
    rows, columns = np.where(ink)
    crop = Image.fromarray(array[rows.min() : rows.max() + 1, columns.min() : columns.max() + 1])
    resized = crop.resize((size, size), Image.Resampling.BILINEAR)
    return (np.array(resized) < 200).astype(np.uint8)


def mapping_from_woff(woff_bytes: bytes) -> dict[int, str]:
    chars, atlas_bits = load_atlas()
    font_file = TTFont(io.BytesIO(woff_bytes))
    otf_buffer = io.BytesIO()
    font_file.save(otf_buffer)
    pillow_font = ImageFont.truetype(io.BytesIO(otf_buffer.getvalue()), 52)
    cmap = font_file.getBestCmap() or {}
    mapping: dict[int, str] = {}
    for codepoint in cmap:
        vector = glyph_bitmap(pillow_font, chr(codepoint)).reshape(-1)
        intersection = (atlas_bits & vector).sum(axis=1)
        union = (atlas_bits | vector).sum(axis=1).clip(min=1)
        iou = intersection / union
        best_index = int(iou.argmax())
        if float(iou[best_index]) >= MATCH_THRESHOLD:
            mapping[codepoint] = chars[best_index]
    return mapping


def decode_text(text: str, mapping: dict[int, str]) -> str:
    if not text:
        return text
    return "".join(mapping.get(ord(character), character) for character in text)
