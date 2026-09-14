"""Shared package requests must preserve every fixture's color requirements."""
from collections import Counter
from pathlib import Path
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from core.assets import packages
from fixtures.player_data import export_colors

PACKAGE = "mysekai__fixture__mdl_shared"


def fixture(identifier, colors):
    return {"id": identifier, "assetbundleName": "mdl_shared",
            "mysekaiFixtureAnotherColors": [{"textureId": value} for value in colors]}


@pytest.fixture
def package_store(monkeypatch):
    calls, reads, textures = [], Counter(), {}

    class Texture:
        def __init__(self, name, color):
            self.name = name
            self.color = color

        def peek_name(self):
            return self.name

        def read(self):
            reads[self.name] += 1
            return SimpleNamespace(image=Image.new("RGBA", (2, 2), (self.color, 0, 0, 255)),
                                   m_TextureSettings=SimpleNamespace(m_FilterMode=1, m_WrapU=1, m_WrapV=1),
                                   m_MipCount=1)

    for number in range(1, 5):
        name = f"tex_shared_{number}"
        textures[name] = Texture(name, number)

    class Store:
        def __init__(self, paths, root):
            assert paths == []

        def package(self, name):
            calls.append(name)
            assert name == PACKAGE
            objects = dict(enumerate(textures.values(), 1))
            return SimpleNamespace(files=[SimpleNamespace(objects=objects,
                kinds={key: "Texture2D" for key in objects})])

    monkeypatch.setattr(packages, "PackageStore", Store)
    return calls, reads, textures, Texture


def png_contents(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes() for path in directory.rglob("*.png")}


def test_shared_package_colors_are_order_independent_and_written_once(tmp_path, package_store):
    calls, reads, _, _ = package_store
    rows = [fixture(1, [2]), fixture(2, [3])]
    forward = export_colors(rows, tmp_path / "bundles", tmp_path / "forward")
    assert set(forward[PACKAGE]) == {"1", "2", "3"}
    assert calls == [PACKAGE]
    assert reads == {"tex_shared_1": 1, "tex_shared_2": 1, "tex_shared_3": 1}
    calls.clear()
    reads.clear()
    reverse = export_colors(list(reversed(rows)), tmp_path / "bundles", tmp_path / "reverse")
    assert forward == reverse
    assert png_contents(tmp_path / "forward") == png_contents(tmp_path / "reverse")
    assert calls == [PACKAGE]
    for number in (1, 2, 3):
        with Image.open(tmp_path / "forward" / forward[PACKAGE][str(number)]["main"]["path"]) as image:
            assert image.getpixel((0, 0)) == (number, 0, 0, 255)


@pytest.mark.parametrize("colors,expected", [
    ([[2]], {1, 2}),
    ([[2, 2], [2]], {1, 2}),
    ([[2, 3], [3, 4], ["2"]], {1, 2, 3, 4}),
    ([[], [3]], {1, 3}),
])
def test_color_requirements_are_a_union_including_the_default(tmp_path, package_store, colors, expected):
    calls, reads, _, _ = package_store
    result = export_colors([fixture(i + 1, values) for i, values in enumerate(colors)], tmp_path / "bundles", tmp_path)
    assert set(result[PACKAGE]) == {str(value) for value in expected}
    assert set(reads) == {f"tex_shared_{value}" for value in expected}
    assert all(count == 1 for count in reads.values())
    assert calls == [PACKAGE]


@pytest.mark.parametrize("emission_exists", [False, True])
def test_missing_named_source_texture_stays_null(tmp_path, package_store, emission_exists):
    _, _, textures, Texture = package_store
    del textures["tex_shared_3"]
    if emission_exists:
        textures["tex_shared_emi_dark_3"] = Texture("tex_shared_emi_dark_3", 3)
    result = export_colors([fixture(1, [2]), fixture(2, [3])], tmp_path / "bundles", tmp_path)
    assert result[PACKAGE]["3"]["main"] is None
    assert not list(tmp_path.rglob("tex_shared_3.png"))
    emission = result[PACKAGE]["3"]["emission"]
    if emission_exists:
        assert emission["path"].endswith("tex_shared_emi_dark_3.png")
        assert (tmp_path / emission["path"]).is_file()
    else:
        assert emission is None


def test_records_without_variants_do_not_trigger_color_exports(tmp_path, package_store):
    calls, reads, _, _ = package_store
    assert export_colors([fixture(1, [])], tmp_path / "bundles", tmp_path) == {}
    assert calls == [] and reads == {}
