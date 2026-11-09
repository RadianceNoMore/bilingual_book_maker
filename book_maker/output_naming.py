"""Portable filenames for model-translated book titles."""

import re
import unicodedata


def safe_title_stem(title):
    """Keep a title within one Windows-compatible filename component."""
    title = unicodedata.normalize("NFC", title)
    title = "".join(
        " " if c.isspace() else c
        for c in title
        if c.isspace() or not unicodedata.category(c).startswith("C")
    )
    title = re.sub(r'[<>:"/\\|?*]', " ", title)
    title = " ".join(title.split()).strip(" .")
    # Leave space for the suffix, extension, and typical output directory paths.
    title = title[:120].rstrip(" .")
    if not title:
        raise ValueError("The translated filename contains no usable title")
    if title.split(".")[0].upper() in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        title = "_" + title
    return title
