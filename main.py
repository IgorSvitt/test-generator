"""Compatibility wrapper for running the package from a source checkout."""

from __future__ import annotations

import sys

from test_generator.main import main


if __name__ == "__main__":
    sys.exit(main())
