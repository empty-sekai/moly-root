"""Compatibility entry point; command implementation lives in cli."""
import sys
from cli.main import main
from cli.legacy import _extract_failed, _print_extract_summary

if __name__ == "__main__":
    sys.exit(main())
