"""Combined command-line entry point: ``python -m pack <build|verify|gc> ...``.

Each of build.py, verify.py, and gc.py also runs standalone
(``python -m pack.build``, ``python -m pack.verify``, ``python -m pack.gc``);
this module exists only for the single combined form.
"""
from __future__ import annotations

import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("build", "verify", "gc"):
        print("usage: python -m pack <build|verify|gc> [args...]", file=sys.stderr)
        return 2
    sub, rest = argv[0], argv[1:]
    if sub == "build":
        from .build import main as sub_main
    elif sub == "verify":
        from .verify import main as sub_main
    else:
        from .gc import main as sub_main
    return sub_main(rest)


if __name__ == "__main__":
    sys.exit(main())
