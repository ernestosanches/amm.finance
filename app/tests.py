#!/usr/bin/env python3
"""Test runner for the AMM game app. Discovers every test_*.py under app/tests/.

  python app/tests.py [-v]
Exits non-zero on any failure.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # so `import backend...` works


def main() -> int:
    verbosity = 2 if "-v" in sys.argv else 1
    loader = unittest.TestLoader()
    suite = loader.discover(os.path.join(HERE, "tests"), pattern="test_*.py", top_level_dir=HERE)
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
