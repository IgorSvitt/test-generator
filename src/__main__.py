"""Allow running the package with python -m test_generator."""

from __future__ import annotations

import sys

from test_generator.main import main


if __name__ == "__main__":
    sys.exit(main())
