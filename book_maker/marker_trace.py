"""Opt-in local trace of marker requests and decoded completions.

The trace exists to separate client-boundary corruption from upstream
assembly.  It is disabled unless ``BBM_MARKER_TRACE_DIR`` names a directory.
Each line is one JSON object containing the sanitized request, the full decoded
completion text, and the same metadata recorded in run diagnostics.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

TRACE_ENV = "BBM_MARKER_TRACE_DIR"
TRACE_FILENAME = "marker-trace.jsonl"
_DROPPED_REQUEST_KEYS = {"api_key", "authorization", "headers", "extra_headers"}


def trace_directory():
    value = os.environ.get(TRACE_ENV, "").strip()
    return Path(value) if value else None


def trace_location():
    directory = trace_directory()
    return str(directory / TRACE_FILENAME) if directory else None


def sanitized_request(request):
    if not isinstance(request, dict):
        return None
    return {
        key: value
        for key, value in request.items()
        if str(key).lower() not in _DROPPED_REQUEST_KEYS
    }


def write_trace(record):
    """Append one trace line; a trace failure never stops translation."""
    directory = trace_directory()
    if directory is None:
        return None
    payload = dict(record)
    payload.setdefault("time", datetime.now(timezone.utc).isoformat())
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / TRACE_FILENAME
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return str(path)
    except Exception:
        return None


def response_digest(text):
    if not isinstance(text, str):
        return None
    return sha256(text.encode("utf-8")).hexdigest()
