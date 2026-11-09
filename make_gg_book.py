"""Compatibility entry point; implementation: book_maker.tools.make_gg_book."""

import sys
from book_maker.tools import make_gg_book as _tool

if __name__ == "__main__":
    raise SystemExit(_tool.main())
else:
    # Preserve imports and monkeypatches of the former standalone module.
    sys.modules[__name__] = _tool
