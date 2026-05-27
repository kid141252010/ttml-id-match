#!/usr/bin/env python3
"""Fill AMLL metadata in TTML files from paired audio metadata."""

from __future__ import annotations

import sys

import ttml_metadata as _ttml_metadata

if __name__ == "__main__":
    raise SystemExit(_ttml_metadata.main())

# Keep patch("fill_ttml_metadata.<name>") pointed at the package facade.
sys.modules[__name__] = _ttml_metadata
