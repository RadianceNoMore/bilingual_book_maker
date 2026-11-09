"""Keep generated state from separate translation runs isolated."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from rich import print


def prepare_book_run(book_path, resume, protected_names=False):
    """Archive generated state on fresh starts; resume leaves it untouched.

    Use exact reserved sidecar names, never a glob that could capture a
    user-supplied glossary, source book, classification plan, or output EPUB.

    Marker runs create no external sidecars.  On a fresh marker run, old
    protected-mode ledgers and pickles are deliberately left where they are:
    the run ignores them rather than migrating or deleting them.
    """
    if resume:
        return None
    if protected_names:
        return None
    book = Path(book_path)
    stem = book.stem
    names = [
        f".{stem}.temp.bin",
        f"{stem}_handoff.md",
        f"{stem}_handoff.md.tmp",
        f"{stem}_approved_glossary.json",
        f"{stem}_approved_glossary.tmp",
        f"{stem}_glossary_review.jsonl",
        f"{stem}_protected_names.json",
        f"{stem}_protected_names.tmp",
        f"{stem}_protected_names_failures.jsonl",
    ]
    existing = [book.parent / name for name in names if (book.parent / name).is_file()]
    if not existing:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive = book.parent / ".run-history" / stem / f"{stamp}-{uuid4().hex[:8]}"
    archive.mkdir(parents=True, exist_ok=False)
    moved = []
    try:
        for path in existing:
            target = archive / path.name
            path.rename(target)
            moved.append((path, target))
    except OSError:
        # Do not leave a half-archived run if a checkpoint is locked, etc.
        for path, target in reversed(moved):
            target.rename(path)
        raise
    print(f"[cyan]Fresh run: archived {len(moved)} previous state file(s) to {archive}[/cyan]")
    return archive
