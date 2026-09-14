"""Canonical asset identities and disjoint input/output roots."""
from pathlib import Path


def separate_output(source, output, overlay=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_dir():
        raise ValueError(f"source is not a directory: {source}")
    for root in (source, Path(overlay).resolve() if overlay is not None else None):
        if root is not None and (output.is_relative_to(root) or root.is_relative_to(output)):
            raise ValueError(f"source/overlay and output directories must not overlap: {root}, {output}")
    return source, output


def asset_path(source, relative):
    source = Path(source).resolve()
    path = (source / relative).resolve()
    if not path.is_relative_to(source) or not path.is_file():
        raise ValueError(f"asset path is outside the source or missing: {relative}")
    logical = path.relative_to(source).as_posix()
    if any(part in ("", ".", "..") for part in logical.split("/")) or "\\" in logical or ":" in logical:
        raise ValueError(f"invalid logical asset path: {logical}")
    return path


def canonical_inputs(source, paths):
    """Collapse references to the same file before assigning package ownership."""
    source = Path(source).resolve()
    canonical = {}
    for relative in paths:
        path = asset_path(source, relative)
        logical = path.relative_to(source).as_posix()
        previous = canonical.get(logical)
        if previous is not None and previous != path:
            raise ValueError(f"conflicting logical asset path: {logical}")
        canonical[logical] = path
    return dict(sorted(canonical.items()))


def output_path(output, relative):
    output = Path(output).resolve()
    path = output / relative
    resolved = path.resolve()
    if not resolved.is_relative_to(output) or resolved == output:
        raise ValueError("manifest/blob path must stay inside the output directory")
    for component in (path, *path.parents):
        if component == output:
            break
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            raise ValueError("output paths must not traverse links inside the output directory")
    return resolved
