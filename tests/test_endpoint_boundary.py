"""Gate: the published tree must not name a vendor asset endpoint.

The host strings below are this scanner's rule data, so they have to stay
verbatim in this file or the gate stops matching anything. The scan therefore
skips this file itself; every other text file in the tree is checked.
"""
from pathlib import Path
import subprocess

FORBIDDEN_HOSTS = ("dailygn.com", "bytedgame.com")
SCANNED_SUFFIXES = {".py", ".md", ".txt", ".toml", ".cfg", ".ini", ".json", ".yml", ".yaml"}
IGNORED_PARTS = {".git", ".pytest_cache", "__pycache__", "node_modules", ".venv", ".validation"}


def scanned_files(root: Path) -> list[Path]:
    myself = Path(__file__).resolve()
    if (root / ".git").exists():
        # Published inventory includes tracked files even if a later ignore
        # pattern matches them, plus eligible new files. Private validation
        # snapshots and dependency/build trees are not publication inputs.
        names = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root).decode("utf-8").split("\0")
        files = [root / name for name in names if name]
    else:
        files = [path for path in root.rglob("*") if not IGNORED_PARTS.intersection(path.parts)]
    return sorted(path for path in files
                  if path.is_file() and path.suffix.lower() in SCANNED_SUFFIXES
                  and path.resolve() != myself)


def test_published_tree_names_no_vendor_asset_endpoint():
    root = Path(__file__).parents[1]
    hits = []
    for path in scanned_files(root):
        try:
            text = path.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeDecodeError):
            continue
        hits.extend((str(path.relative_to(root)), host) for host in FORBIDDEN_HOSTS if host in text)
    assert not hits, hits


def test_the_gate_can_still_see_a_planted_endpoint(tmp_path):
    planted = tmp_path / "planted.md"
    planted.write_text(f"https://cdn.{FORBIDDEN_HOSTS[0]}/obj/bundles\n", encoding="utf-8")
    assert scanned_files(tmp_path) == [planted]
    assert any(host in planted.read_text(encoding="utf-8") for host in FORBIDDEN_HOSTS)
