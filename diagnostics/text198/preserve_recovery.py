"""Preserve the text198 recovery EPUB read-only with a digest manifest.

Run from the repository root:

    python diagnostics/text198/preserve_recovery.py

The source recovery file is only read.  A byte-for-byte copy is written under
diagnostics/text198/ and verified by SHA-256.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from book_maker.marker_bundle import read_state, validate_state

REPO = Path(__file__).resolve().parents[2]
ONGOING = REPO / "ongoing"
DEST = Path(__file__).with_name("recovery-preserved.epub")
MANIFEST = Path(__file__).with_name("recovery-manifest.json")


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    candidates = sorted(ONGOING.glob("*_bilingual_temp.epub"))
    if not candidates:
        raise SystemExit(f"no recovery EPUB found in {ONGOING}")
    # The most recently written candidate is the text198 fresh-run recovery.
    source = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    source_digest = digest(source)
    shutil.copy2(source, DEST)
    copied_digest = digest(DEST)
    if copied_digest != source_digest:
        raise SystemExit("copied recovery digest does not match the source")

    state = read_state(DEST)
    validate_state(state)
    windows = []
    for record in state.get("source_windows", {}).get("windows", []):
        units = record.get("units", [])
        windows.append(
            {
                "window_id": record.get("window_id"),
                "status": record.get("status"),
                "units": len(units),
                "accepted": len(record.get("accepted_unit_ids") or []),
                "outgoing_summary": bool(record.get("outgoing_summary")),
            }
        )
    checkpoint_ids = {
        identity
        for identity in state.get("checkpoint", {}).get("accepted_job_ids", [])
        if isinstance(identity, str)
    }
    journal_ids = {
        entry.get("identity")
        for entry in state.get("paragraph_recovery", {}).get("entries", [])
        if isinstance(entry.get("identity"), str)
    }
    run = state.get("run", {})
    manifest = {
        "preserved_at": datetime.now(timezone.utc).isoformat(),
        "source_path": str(source),
        "copy_path": str(DEST),
        "source_sha256": source_digest,
        "copy_sha256": copied_digest,
        "size_bytes": DEST.stat().st_size,
        "run": {
            "run_id": run.get("run_id"),
            "mode": run.get("mode"),
            "resume": run.get("resume"),
            "status": run.get("status"),
            "source_sha256": run.get("source_sha256"),
        },
        "windows": windows,
        "checkpoint_ids": len(checkpoint_ids),
        "journal_ids": len(journal_ids),
        "additional_journal_ids": len(journal_ids - checkpoint_ids),
        "distinct_durable_ids": len(checkpoint_ids | journal_ids),
    }
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"Source file untouched: sha256={source_digest}")


if __name__ == "__main__":
    main()
