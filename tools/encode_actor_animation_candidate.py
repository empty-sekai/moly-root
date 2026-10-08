"""Create a new opt-in lossy actor GLB candidate; never change production assets."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from chara.animation_candidate import encode_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--lossy-rotation-snorm16", action="store_true", help="Required explicit opt-in; not lossless compression")
    parser.add_argument("--max-angle-degrees", type=float, default=0.01)
    parser.add_argument("--max-norm-squared-error", type=float, default=2e-4)
    args = parser.parse_args()
    try:
        report = encode_candidate(args.input, args.output, report_path=args.report,
            allow_lossy=args.lossy_rotation_snorm16, max_angle_degrees=args.max_angle_degrees,
            max_norm_squared_error=args.max_norm_squared_error)
    except (ValueError, OSError, KeyError, IndexError) as error:
        parser.error(str(error))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
