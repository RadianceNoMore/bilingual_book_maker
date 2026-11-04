"""Offline repair of a contaminated marker recovery bundle.

This helper never calls the provider and never changes accepted book text,
the catalogue, the checkpoint, or the recovery journal. It classifies the
saved context seed and summary with the shared standalone-provider-error
rules, replaces recognized provider boilerplate with a pending-repair
record, and rewrites only ``META-INF/bbm-marker-state.json`` inside the
bundle with a verified atomic replacement. The next normal ``-Resume`` run
then rebuilds context from accepted evidence automatically:

    python -m book_maker.marker_repair "ongoing/<recovery>.epub"

The original bundle is backed up under ``ongoing/backup`` before the run
replaces anything; that directory is excluded from resume discovery because
resume only scans ``ongoing/*.epub`` directly.
"""

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from book_maker.marker_bundle import BundleError, read_state, validate_state

class RepairNotNeeded(Exception):
    """The named bundle has no recognized contamination to repair."""


_DIAGNOSTIC_LIMIT = 40


def _now():
    return datetime.now(timezone.utc).isoformat()


def _quarantine_diagnostics(state, field, text, matched):
    """Record the bounded poisoned text in diagnostics only."""
    failures = state.setdefault("diagnostics", {}).setdefault("failures", [])
    failures.append(
        {
            "time": _now(),
            "stage": "offline-context-restore",
            "reason": (
                "recognized provider error message in the saved session "
                f"field {field!r}"
            ),
            "response": (text or "")[:8000],
            "request": "",
            "response_metadata": None,
        }
    )
    del failures[:-_DIAGNOSTIC_LIMIT]


def repair_state(state):
    """Clean poisoned context fields in ``state``; return changed fields.

    Only the session context fields and the pending-repair record change.
    Old ``status=ok`` compaction events are preserved as historical
    evidence; the field content itself decides contamination.
    """
    from book_maker.provider_refusal import standalone_provider_error

    session = state.get("session")
    if not isinstance(session, dict):
        raise BundleError("Marker state session is malformed")
    poisoned = {}
    for field in ("seed", "last_summary"):
        text = session.get(field)
        if isinstance(text, str) and text:
            matched = standalone_provider_error(text)
            if matched:
                poisoned[field] = matched
    if not poisoned:
        return []
    for field, matched in poisoned.items():
        _quarantine_diagnostics(state, field, session.get(field), matched)
        session[field] = ""
    record = session.get("context_repair")
    if not isinstance(record, dict):
        record = {}
    record.update(
        {
            "pending": True,
            "purpose": "offline-cleanup",
            "reason": (
                "recognized provider error message in saved context "
                "field(s): " + ", ".join(sorted(poisoned))
            ),
            "marked_at": _now(),
        }
    )
    session["context_repair"] = record
    return sorted(poisoned)


def repair_bundle(path, *, backup_dir=None):
    """Repair one recovery EPUB; return ``(changes, backup_path)``.

    The named bundle must be a complete marker bundle. When neither context
    field is recognized provider boilerplate nothing is touched and
    :class:`RepairNotNeeded` is raised. Otherwise the original is copied into
    the backup directory and only the embedded state is replaced, with the
    replacement written to a temporary file and verified (the embedded state
    re-reads and passes bundle validation) before it atomically replaces the
    original.
    """
    from book_maker.provider_refusal import standalone_provider_error

    path = Path(path)
    state = read_state(path)
    validate_state(state)
    session = state.get("session") or {}
    changes = repair_state(state)
    needs = [
        field
        for field in ("seed", "last_summary")
        if isinstance(session.get(field), str)
        and standalone_provider_error(session.get(field))
    ]
    if not needs and not changes:
        raise RepairNotNeeded(
            f"{path}: no recognized provider error message in the saved "
            "context fields; nothing was changed"
        )
    changes = sorted(set(needs) | set(changes))
    from book_maker.marker_bundle import write_bundle

    backup_dir = (
        (path.parent / "backup") if backup_dir is None else Path(backup_dir)
    )
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{path.stem}-{stamp}{path.suffix}"
    shutil.copy2(path, backup_path)
    # write_bundle copies every archive member except mimetype/state, writes
    # the replacement state, verifies the candidate bundle, and atomically
    # replaces the destination — the same verified path recovery writes use.
    write_bundle(path, path, state, validate=True)
    return changes, backup_path


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if not argv:
        print("usage: python -m book_maker.marker_repair <recovery.epub> ...")
        return 2
    exit_code = 0
    for argument in argv:
        try:
            changes, backup = repair_bundle(argument)
        except RepairNotNeeded as error:
            print(f"no repair needed: {error}")
            continue
        except Exception as error:
            print(f"{argument}: repair failed: {error}")
            exit_code = 1
            continue
        print(
            f"repaired {argument}: cleared {', '.join(changes)}; "
            f"backup {backup}. The next normal resume automatically "
            "rebuilds context from accepted translations."
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
