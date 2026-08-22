import json
from pathlib import Path


def test_public_tree_has_no_private_markers():
    root = Path(__file__).parents[1]
    files = [*root.joinpath("src").rglob("*.py"), *root.joinpath("docs").rglob("*.md"),
             *(p for suffix in ("*.js", "*.html", "*.css", "*.md")
               for p in root.joinpath("examples").rglob(suffix)),
             root / "README.md", root / "README.en.md", root / "THIRD_PARTY_NOTICES.md"]
    forbidden = ("F:/", "F:\\", "allium", "RE-jp", "pseudo", "daily" + "gn.com", "byted" + "game.com")
    hits = [(path, marker) for path in files for marker in forbidden
            if marker in path.read_text(encoding="utf-8")]
    assert not hits, hits


def test_public_tree_has_no_embedded_asset_bundle_manifest():
    root = Path(__file__).parents[1]
    ignored = {".git", ".pytest_cache", "__pycache__", "local-data"}
    files = [path for path in root.rglob("*")
             if path.is_file() and not ignored.intersection(path.parts)]

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
    files = [*root.rglob("*.md"), *root.joinpath("src").rglob("*.py")]
    forbidden = ("closure", "闭包")
    hits = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".md":
            hits.extend((path, marker) for marker in forbidden if marker in text.lower())
        elif "help=" in text:
            hits.extend((path, marker) for marker in forbidden if marker in text.lower())
    assert not hits, hits
