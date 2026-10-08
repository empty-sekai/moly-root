#!/usr/bin/env python3
"""Read-only CLI for the shared canonical coordinate validator."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core.coordinate_contract import (CONTRACT, SOURCE, UNITS, main, read_glb,
    validate_attach, validate_glb, validate_index, validate_json_subcontract,
    validate_snapshot)


if __name__ == "__main__":
    raise SystemExit(main())
