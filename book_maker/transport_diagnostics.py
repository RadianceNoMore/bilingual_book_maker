"""Readable, secret-free descriptions of provider transport failures.

A connection error is not a paragraph-level validation failure.  The outer
OpenAI exception usually says only ``Connection error.``; the useful facts are
the endpoint that was dialled and the cause chain underneath.  These helpers
keep that evidence printable without ever echoing credentials.
"""

from __future__ import annotations

import socket
import ssl
from urllib.parse import urlsplit, urlunsplit

from book_maker.redaction import redact

DEFAULT_TRANSPORT_ATTEMPTS = 3

# Class names are used because the vendor SDKs wrap the same httpx/httpcore
# errors differently across versions, and the marker workflow must not import
# every optional provider SDK just to classify an exception.  The built-in
# isinstance checks below cover subclasses that are not named here.
_TRANSPORT_TYPE_NAMES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "BrokenPipeError",
        "ConnectError",
        "ConnectTimeout",
        "ConnectionAbortedError",
        "ConnectionError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "LocalProtocolError",
        "NetworkError",
        "PoolTimeout",
        "ProtocolError",
        "ProxyError",
        "ReadError",
        "ReadTimeout",
        "RemoteProtocolError",
        "SSLCertVerificationError",
        "SSLError",
        "TimeoutError",
        "TransportError",
        "UnsupportedProtocol",
        "WriteError",
        "WriteTimeout",
        "gaierror",
        "herror",
    }
)

_ENDPOINT_FALLBACK = "endpoint unavailable (model exposes no api_base/base_url)"
_CHAIN_LIMIT = 6
_MESSAGE_LIMIT = 240
_ENDPOINT_PATH_LIMIT = 80


def is_transport_failure(error):
    """Whether ``error`` means no response was received from the endpoint.

    Only connection/DNS/socket/TLS/timeout classes qualify.  Provider HTTP
    responses (a rate limit, a 500 with a body) and programming errors such as
    ``RuntimeError`` deliberately do not: those keep the paragraph-level
    retry and charging behavior.
    """
    if error is None:
        return False
    for cls in type(error).__mro__:
        if cls.__name__ in _TRANSPORT_TYPE_NAMES:
            return True
    try:
        return isinstance(
            error,
            (
                ConnectionError,
                TimeoutError,
                socket.gaierror,
                socket.herror,
                ssl.SSLError,
            ),
        )
    except TypeError:  # pragma: no cover - defensive against odd SDK shims
        return False


def _bounded_text(text, limit=200):
    text = redact(str(text)).replace("\n", " ").strip()
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def sanitize_endpoint(value):
    """Return a URL safe to print: origin and path, never credentials/query."""
    if value is None:
        return _ENDPOINT_FALLBACK
    text = redact(str(value)).strip()
    if not text:
        return _ENDPOINT_FALLBACK
    try:
        parts = urlsplit(text)
    except ValueError:
        return _bounded_text(text)
    if not (parts.scheme and parts.netloc):
        return _bounded_text(text)
    try:
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return _bounded_text(text)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host + (f":{port}" if port else "")
    path = parts.path or ""
    if len(path) > _ENDPOINT_PATH_LIMIT:
        path = path[: _ENDPOINT_PATH_LIMIT - 3].rstrip() + "..."
    return urlunsplit((parts.scheme, netloc, path, "", ""))


def provider_endpoint(model):
    """Resolve the endpoint this run actually dialled, without inventing one."""
    value = getattr(model, "api_base", None)
    if value:
        return sanitize_endpoint(value)
    for attribute in ("openai_client", "client"):
        client = getattr(model, attribute, None)
        if client is None:
            continue
        value = getattr(client, "base_url", None)
        if value:
            return sanitize_endpoint(value)
    return _ENDPOINT_FALLBACK


def exception_chain(error, limit=_CHAIN_LIMIT):
    """Render ``__cause__``/``__context__`` as redacted ``type: message`` rows."""
    if error is None:
        return "no exception"
    rows = []
    seen = set()
    current = error
    while current is not None and len(rows) < max(1, int(limit)):
        marker = id(current)
        if marker in seen:
            break
        seen.add(marker)
        name = type(current).__name__
        message = redact(str(current)).replace("\n", " ").strip()
        if len(message) > _MESSAGE_LIMIT:
            message = message[: _MESSAGE_LIMIT - 3].rstrip() + "..."
        row = f"{name}: {message}" if message else name
        if not rows or rows[-1] != row:
            rows.append(row)
        next_error = getattr(current, "__cause__", None)
        if next_error is None:
            next_error = getattr(current, "__context__", None)
        if next_error is current:
            break
        current = next_error
    return " <- ".join(rows)
