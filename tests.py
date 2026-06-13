#!/usr/bin/env python3
"""Test runner: discover and run every test_*.py in this directory.

Usage:
  python tests.py        # normal
  python tests.py -v     # verbose
Exit code is non-zero if any test fails, so this is CI-friendly.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    sys.path.insert(0, HERE)
    verbosity = 2 if "-v" in sys.argv else 1
    suite = unittest.TestLoader().discover(start_dir=HERE, pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
