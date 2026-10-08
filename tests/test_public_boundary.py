import json
from pathlib import Path


# Scanner rule literals are allowed only in the scanners that implement them.
SCANNER_FILES = {"tests/test_public_boundary.py", "tests/test_endpoint_boundary.py"}
FORBIDDEN = ("F:/", "F:\\", "allium", "RE-jp", "pseudo", "daily" + "gn.com", "byted" + "game.com")


def private_marker_hits(root):
    hits = []
    for path in public_files(root):
        if path.relative_to(root).as_posix() in SCANNER_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits.extend((path, marker) for marker in FORBIDDEN if marker in text)
    return hits


def public_files(root):
    """Inspect shippable files, including tracked files matched by new ignores."""
    import subprocess
    if (root / ".git").exists():
        names = subprocess.check_output(["git", "ls-files", "--cached", "--others",
                                         "--exclude-standard", "-z"], cwd=root).decode("utf-8").split("\0")
        return [root / name for name in names if name and (root / name).is_file()]
    ignored = {".git", ".pytest_cache", "__pycache__", "local-data", ".validation", "node_modules"}
    return [path for path in root.rglob("*")
            if path.is_file() and not ignored.intersection(path.relative_to(root).parts)]


def test_public_tree_has_no_private_markers():
    root = Path(__file__).parents[1]
    hits = private_marker_hits(root)
    assert not hits, hits


def test_private_marker_gate_covers_tools_integration_and_tests(tmp_path):
    for folder in ("tools", "integration", "tests"):
        path = tmp_path / folder / "sample.txt"
        path.parent.mkdir()
        path.write_text(FORBIDDEN[0] + "synthetic/example", encoding="utf-8")
    assert len(private_marker_hits(tmp_path)) == 3


def test_public_tree_has_no_embedded_asset_bundle_manifest():
    root = Path(__file__).parents[1]
    # Inspect what can actually enter the public tree, not ignored original
    # source caches created by the documented download CLI. Tracked files are
    # always inspected even when a later ignore pattern would match them.
    files = public_files(root)

    filename_hits = [path for path in files if "AssetBundleInfo" in path.name]
    json_hits = []
    for path in files:
        if path.suffix.lower() != ".json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("bundles"), list) \
                and len(data["bundles"]) > 100:
            json_hits.append(path)

    assert not filename_hits and not json_hits, filename_hits + json_hits


def test_user_visible_text_has_no_legacy_terms():
    root = Path(__file__).parents[1]
    files = [path for path in public_files(root)
             if path.suffix == ".md" or (path.suffix == ".py" and path.is_relative_to(root / "src"))]
    forbidden = ("closure", "闭包")
    hits = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".md":
            hits.extend((path, marker) for marker in forbidden if marker in text.lower())
        elif "help=" in text:
            hits.extend((path, marker) for marker in forbidden if marker in text.lower())
    assert not hits, hits
