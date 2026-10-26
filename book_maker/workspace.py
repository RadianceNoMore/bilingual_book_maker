"""Keep translation sources and resume data independent of external files."""

import hashlib
import os
import shutil
import tempfile
from pathlib import Path


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy(source, target):
    """Publish a complete copy; never leave a partial source/checkpoint."""
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".copy-")
    try:
        with os.fdopen(fd, "wb") as destination, source.open("rb") as original:
            shutil.copyfileobj(original, destination)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_source(book_name, input_dir="input", resume=False):
    """Copy a source once, preserving existing working copies and progress.

    Identical basenames with different contents receive a content suffix.
    A missing external source can still resume from its saved working copy.
    """
    source = Path(book_name).expanduser().resolve()
    directory = Path(input_dir).expanduser().resolve()
    target = directory / source.name
    if not source.is_file():
        if resume and target.is_file():
            # A basename alone cannot identify which of several books was used.
            if list(directory.glob(f"{source.stem}-*{source.suffix}")):
                raise ValueError(
                    "Several working copies share this name; use --book_name "
                    "with the exact file in the input directory."
                )
            return target
        raise FileNotFoundError(f"The book {str(source)!r} does not exist")
    directory.mkdir(parents=True, exist_ok=True)
    if source == target:
        return source
    if target.exists() and _digest(source) != _digest(target):
        target = directory / f"{source.stem}-{_digest(source)[:12]}{source.suffix}"
        if target.exists() and _digest(source) != _digest(target):
            raise ValueError(f"Working copy collision: {target}")
    if not target.exists():
        _copy(source, target)
    # Plans are operator-edited decisions. Keep them even on a fresh run.
    sidecars = [(source.with_name(source.stem + "_plan.json"),
                 target.with_name(target.stem + "_plan.json"))]
    if resume:
        sidecars.append((source.with_name(f".{source.stem}.temp.bin"),
                         target.with_name(f".{target.stem}.temp.bin")))
        sidecars.append((source.with_name(f"{source.stem}_handoff.md"),
                         target.with_name(f"{target.stem}_handoff.md")))
    for original, destination in sidecars:
        if original.is_file() and not destination.exists():
            _copy(original, destination)
    return target
