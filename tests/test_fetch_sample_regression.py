"""Regression checks against a real AssetBundleInfoNew sample.

The sample is never part of this repository. Point MOLY_SAMPLE_MANIFEST at a
manifest you exported yourself; without it these checks skip.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from core.fetch import Manifest, build_download_url

SAMPLE_VARIABLE = "MOLY_SAMPLE_MANIFEST"
ASSET_BASE_URL = "https://example.invalid/AssetBundle/1.0.0/Release/example_online"


@pytest.fixture(scope="module")
def sample() -> Manifest:
    configured = os.environ.get(SAMPLE_VARIABLE)
    if not configured:
        pytest.skip(f"{SAMPLE_VARIABLE} is not configured")
    path = Path(configured)
    if not path.is_file():
        pytest.skip(f"{SAMPLE_VARIABLE} does not point to a file")
    manifest = Manifest.load(path)
    if not manifest.entries:
        pytest.skip(f"{SAMPLE_VARIABLE} carries no bundle entries")
    return manifest


@pytest.fixture(scope="module")
def sample_roots(sample: Manifest) -> list[str]:
    roots = sample.roots()
    if not roots:
        pytest.skip("the sample manifest carries no character roots")
    return roots


def test_sample_manifest_resolves_roots_in_dependency_order(sample, sample_roots):
    ordered = sample.required_bundles(sample_roots)
    position = {entry.bundle_name: index for index, entry in enumerate(ordered)}
    assert set(sample_roots) <= set(position)
    for entry in ordered:
        for dependency in entry.dependencies:
            assert position[dependency] < position[entry.bundle_name]


def test_sample_manifest_urls_join_only_the_prefix_and_manifest_fields(sample, sample_roots):
    for entry in sample.required_bundles(sample_roots):
        url = build_download_url(ASSET_BASE_URL, entry)
        tail = "/".join(part.strip("/") for part in (entry.download_path, entry.bundle_name) if part.strip("/"))
        assert url == f"{ASSET_BASE_URL}/{tail}"
        assert url.endswith(entry.bundle_name)
