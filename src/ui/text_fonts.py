"""Independent, immutable display-font catalogue from operator-supplied fonts."""
from pathlib import Path

from core.content import canonical_bytes, content_hash, document_hash


def coverage_ranges(points):
    result = []
    for point in sorted(set(points)):
        if not 0 <= point <= 0x10FFFF or 0xD800 <= point <= 0xDFFF:
            continue
        if result and result[-1][1] + 1 == point:
            result[-1][1] = point
        else:
            result.append([point, point])
    return result


def build_font_catalog(base_fonts, fonts, license_file, output):
    from fontTools.ttLib import TTFont
    from io import BytesIO

    output = Path(output)
    files = {}

    def font(path):
        data = Path(path).read_bytes()
        if not 0 < len(data) <= 32 * 1024 * 1024:
            raise ValueError('text font is empty or exceeds the runtime font limit')
        with TTFont(BytesIO(data), lazy=False) as parsed:
            if parsed.flavor or 'glyf' not in parsed:
                raise ValueError('text fonts must be uncompressed TrueType fonts')
            # Glyph zero is the missing-glyph marker, not coverage.
            points = [point for point, glyph in parsed.getBestCmap().items() if parsed.getGlyphID(glyph) != 0]
        return data, coverage_ranges(points)

    license_data = Path(license_file).read_bytes()
    license_data.decode('utf-8', errors='strict')
    if not 0 < len(license_data) <= 65536:
        raise ValueError('font license is empty or too large')

    def store(data, suffix):
        digest = content_hash(data)
        path = f'objects/{digest}.{suffix}'
        files[path] = data
        return {'path': path, 'sha256': digest, 'bytes': len(data)}

    license_ref = store(license_data, 'txt')
    base = {}
    for path in base_fonts:
        data, coverage = font(path)
        base[content_hash(data)] = {'coverage': coverage}
    supplemental = []
    for path in fonts:
        data, coverage = font(path)
        supplemental.append({**store(data, 'ttf'), 'coverage': coverage, 'license': license_ref})
    if not 1 <= len(base) <= 16 or not 1 <= len(supplemental) <= 8:
        raise ValueError('font catalogue needs 1-16 base fonts and 1-8 supplemental fonts')
    if len({row['sha256'] for row in supplemental}) != len(supplemental):
        raise ValueError('duplicate supplemental font')
    root = {'schema': 'moly-text-fonts/1', 'baseFonts': base, 'fonts': supplemental}
    root['revision'] = document_hash(root)
    data = canonical_bytes(root)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError('font catalogue exceeds its document size limit')
    files[f"revisions/{root['revision']}.json"] = data
    files['latest.json'] = data
    for name, content in files.items():
        path = output / name
        if path.exists() and path.read_bytes() != content:
            raise ValueError('font output differs; use a fresh publication directory')
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
    return root
