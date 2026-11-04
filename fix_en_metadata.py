"""Compatibility entry point; implementation: book_maker.tools.fix_en_metadata."""

import sys
from book_maker.tools import fix_en_metadata as _tool

if __name__ == "__main__":
    raise SystemExit(_tool.main())
else:
    # Preserve imports and monkeypatches of the former standalone module.
    sys.modules[__name__] = _tool
