"""Per-extension packing rules, loaded from data rather than branched in code.

Which codec a class gets, whether the origin serves it with
``Content-Encoding: br``, and what content transform (if any) is already
baked into its bytes are being *measured*, not decided here -- see
``categories.toml``. This module only loads that table and looks values up
in it; changing a class's treatment is a data edit there, never a code edit
here.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_TABLE_PATH = Path(__file__).with_name("categories.toml")

VALID_CODECS = {"identity", "gzip", "brotli"}
VALID_HTTP_ENCODINGS = {"identity", "br"}
VALID_XF = {None, "meshopt", "draco", "ktx2"}


@dataclass(frozen=True)
class Rule:
    codec: str
    http_encoding: str
    xf: Optional[str] = None

    def __post_init__(self):
        if self.codec not in VALID_CODECS:
            raise ValueError(f"unknown codec {self.codec!r} (want one of {sorted(VALID_CODECS)})")
        if self.http_encoding not in VALID_HTTP_ENCODINGS:
            raise ValueError(
                f"unknown http_encoding {self.http_encoding!r} (want one of {sorted(VALID_HTTP_ENCODINGS)})")
        if self.xf not in VALID_XF:
            raise ValueError(f"unknown xf {self.xf!r} (want one of {sorted(VALID_XF)})")
        if self.http_encoding == "br" and self.codec != "identity":
            raise ValueError(
                f"invalid rule: http_encoding='br' requires codec='identity', "
                f"got codec={self.codec!r} (never both -- double compression)")


class CategoryTable:
    """Extension -> Rule lookup, with one fallback rule for everything else."""

    def __init__(self, default: Rule, by_extension: dict[str, Rule]):
        self.default = default
        self.by_extension = by_extension

    def classify(self, extension: str) -> Rule:
        """*extension* may be a bare suffix ("json") or a dotted one (".json")."""
        ext = extension.lower().lstrip(".")
        return self.by_extension.get(ext, self.default)

    def __repr__(self):
        return f"CategoryTable(default={self.default!r}, extensions={sorted(self.by_extension)!r})"


def _rule_from_row(row: dict) -> Rule:
    return Rule(
        codec=row.get("codec", "identity"),
        http_encoding=row.get("http_encoding", "identity"),
        xf=row.get("xf"),
    )


def _assert_no_brotli(default: Rule, extensions: dict[str, Rule]) -> None:
    """Construction-time tripwire on the BUNDLED default table only (never on
    a caller-supplied ``--categories`` override -- see load_categories).

    web/src/codec.ts ships zero third-party runtime dependencies, so the
    shipped web client has no bundled brotli decoder: its default fallback
    loader throws unless something has called setBrotliFallbackLoader()
    first (see codec.ts's defaultBrotliFallbackLoader). The client's own
    correctness therefore depends on this table never assigning
    codec='brotli' -- a manifest that did would decode fine in this
    process's own round-trip check (build.py encodes AND decodes locally)
    but fail at runtime on any browser lacking native
    DecompressionStream('brotli') support (Chrome, at this writing).

    This does not forbid codec='brotli' outright -- a caller who points
    --categories at a different table, paired with a client build that has
    actually injected a decoder, is untouched by this check.
    """
    offenders = [ext for ext, rule in extensions.items() if rule.codec == "brotli"]
    if default.codec == "brotli":
        offenders = ["[default]"] + offenders
    if offenders:
        raise ValueError(
            f"the bundled default categories table ({DEFAULT_TABLE_PATH}) assigns codec='brotli' "
            f"to: {', '.join(offenders)} -- the shipped web client has no bundled brotli decoder "
            "(web/src/codec.ts ships zero third-party runtime dependencies); either remove this "
            "from the default table, or ship a brotli decoder with the client first")


def load_categories(path=None) -> CategoryTable:
    """Load a category table from a TOML file (default: the bundled one)."""
    table_path = Path(path) if path else DEFAULT_TABLE_PATH
    with open(table_path, "rb") as fh:
        doc = tomllib.load(fh)
    default = _rule_from_row(doc.get("default", {}))
    extensions = {
        str(ext): _rule_from_row(row)
        for ext, row in doc.get("extensions", {}).items()
    }
    if path is None:
        _assert_no_brotli(default, extensions)
    return CategoryTable(default=default, by_extension=extensions)
