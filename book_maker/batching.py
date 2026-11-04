"""Request bounds shared by EPUB body and navigation translation.

These are source-text estimates, not model context-window guarantees. A single
oversized unit stays intact so paragraph and inline-marker identities survive.
"""

DEFAULT_REQUEST_MAX_UNITS = 40
DEFAULT_REQUEST_MAX_TOKENS = 4000


def request_slices(texts, max_units, max_tokens, estimate):
    """Yield contiguous slices without reordering or breaking a unit."""
    start, tokens = 0, 0
    for index, text in enumerate(texts):
        size = estimate(text)
        if index > start and (index - start >= max_units or tokens + size > max_tokens):
            yield slice(start, index)
            start, tokens = index, 0
        tokens += size
    if start < len(texts):
        yield slice(start, len(texts))
