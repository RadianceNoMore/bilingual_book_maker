"""Build a readable preview epub from a RUNNING translation's checkpoint.

The runner banks progress in `<book>.temp.bin` every ~20 units but only
writes a book at the end (or on crash). This script copies that checkpoint
plus the source epub into a temp dir, replays the banked translations
through the repo's own plan/splice code (`_save_temp_book`), and writes
`<stem>_preview.epub` -- without touching the live run. Rerun anytime.

Usage:
    python preview_progress.py <source.epub> [<preview.epub>]
                             [--translate-tags h1,h2,h3,p,li]

--translate-tags MUST match the running run's flags, or the job-id check
(the loader's own) refuses instead of splicing wrong text under
wrong paragraphs.

NOTE: only resumable modes ever write the checkpoint this reads
(--accumulated_num 1, or plan mode). Batched tag mode (accumulated_num > 1
without --plan-classify) keeps everything in memory and banks nothing --
there is no checkpoint to preview until the book completes.
"""

from __future__ import annotations

import argparse
import pickle
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "bilingual_book_maker"))


def robust_copy_bin(src: Path, dst: Path, timeout: float = 90.0) -> dict:
    """Copy a possibly-mid-write pickle checkpoint; retry till it loads."""
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            shutil.copy2(src, dst)
            with open(dst, "rb") as f:
                state = pickle.load(f)
            if not isinstance(state, dict) or not isinstance(
                state.get("translations"), list
            ):
                raise ValueError("not a translation checkpoint")
            return state
        except Exception as e:  # torn read while the runner writes: retry
            last_err = e
            time.sleep(2)
    raise SystemExit(f"could not read a clean checkpoint from {src}: {last_err}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("book", help="source epub the runner is translating")
    ap.add_argument("preview", nargs="?",
                    help="preview epub to write (default: <stem>_preview.epub)")
    ap.add_argument("--translate-tags", default="h1,h2,h3,p,li",
                    help="MUST match the running run (default: %(default)s)")
    ns = ap.parse_args()

    src = Path(ns.book)
    if not src.is_file():
        print(f"not found: {src}")
        return 1
    bin_path = src.parent / f".{src.stem}.temp.bin"
    if not bin_path.is_file():
        print(f"no checkpoint yet ({bin_path.name} missing): "
              "nothing banked, try again after the first batch lands")
        return 1
    preview = Path(ns.preview) if ns.preview else src.parent / f"{src.stem}_preview.epub"

    from ebooklib import epub as epub_lib
    from book_maker.loader.epub_loader import EPUBBookLoader, is_our_colophon
    from book_maker.translator.gemini_translator import Gemini

    tmp = Path(tempfile.mkdtemp(prefix="bbm_preview_"))
    try:
        book_copy = tmp / "book.epub"
        shutil.copy2(src, book_copy)
        state = robust_copy_bin(bin_path, tmp / ".book.temp.bin")
        n_banked = len(state["translations"])
        print(f"checkpoint: {n_banked} banked translations")

        loader = EPUBBookLoader(
            str(book_copy), Gemini, "preview-no-network", True,
            language="english", single_translate=True, source_lang="ko",
        )
        loader.translate_tags = ns.translate_tags

        # The loader's own alignment gate: checkpoint slots must be a
        # prefix of this plan, or flags differ and splicing would corrupt.
        document_items = [
            it for it in loader.origin_book.get_items_of_type(
                epub_lib.ITEM_DOCUMENT)
            if not is_our_colophon(it)
        ]
        plans = loader._build_translation_plan(
            document_items, loader.translate_tags.split(","))
        planned = [job.job_id for plan in plans for job in plan.jobs]
        ckpt_ids = loader._checkpoint_job_ids
        if ckpt_ids != planned[: len(ckpt_ids)]:
            print("[red]checkpoint job ids are not a prefix of this plan: "
                  "flags differ from the running run "
                  "(--translate-tags?). Delete nothing; fix flags and retry.")
            return 1
        total = len(planned)
        print(f"plan: {total} units, preview covers "
              f"{n_banked} ({100.0 * n_banked / max(total, 1):.1f}%)")

        loader._save_temp_book()
        built = tmp / "book_bilingual_temp.epub"
        shutil.copy2(built, preview)
        print(f"preview: {preview}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
