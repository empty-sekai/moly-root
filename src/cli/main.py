"""Grouped public entry point, retaining the existing flat commands."""
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"source", "depot"}:
        from .pipeline import main as pipeline_main
        return pipeline_main(argv)
    if argv and argv[0] == "export":
        argv = argv[1:]
        if argv and argv[0] in {"talks", "communication-actions", "text-catalog", "official-text", "text-runtime"}:
            from .pipeline import main as pipeline_main
            return pipeline_main(["export", *argv])
        # Existing direct readers stay available through the new command
        # group while individual exports move to declared pipeline stages.
    from .legacy import main as legacy_main
    return legacy_main(argv)
