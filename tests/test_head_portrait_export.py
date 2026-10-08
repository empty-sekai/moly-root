"""Fail closed on drift or clipped captures; bottom hair is deliberately allowed."""
import importlib.util
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

TOOL = Path(__file__).resolve().parents[1] / "tools" / "head-portraits" / "finalize.py"
spec = importlib.util.spec_from_file_location("head_portrait_finalize", TOOL)
heads = importlib.util.module_from_spec(spec)
spec.loader.exec_module(heads)


def portrait():
    image = Image.new("RGBA", (512, 512))
    ImageDraw.Draw(image).rectangle((128, 100, 383, 511), fill=(140, 80, 60, 255))
    return image


def test_transparent_head_and_trailing_hair_pass():
    result = heads.image_metrics(portrait())
    assert result["headBoundaryPixels"] == 0
    assert result["hairOnBorderPixels"] == 256, "permitted hair overflow is still reported"
    assert result["bounds"] == [128, 100, 384, 512]


@pytest.mark.parametrize("pixel", [(0, 200), (511, 200), (200, 0)])
def test_crown_and_side_clipping_fail(pixel):
    image = portrait()
    image.putpixel(pixel, (255, 255, 255, 1))
    with pytest.raises(ValueError, match="clipping"):
        heads.image_metrics(image)


def test_missing_alpha_or_empty_frame_fails():
    with pytest.raises(ValueError, match="RGBA"):
        heads.image_metrics(portrait().convert("RGB"))
    with pytest.raises(ValueError, match="opaque head"):
        heads.image_metrics(Image.new("RGBA", (512, 512)))


def test_hash_drift_and_path_escape_fail(tmp_path):
    source = tmp_path / "model.glb"
    source.write_bytes(b"original")
    hashes = {"model.glb": heads.digest(source.read_bytes())}
    heads.check_hashes(tmp_path, hashes)
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        heads.check_hashes(tmp_path, hashes)
    with pytest.raises(ValueError, match="relative asset"):
        heads.local_file(tmp_path, "../model.glb")


def test_finalization_does_not_accept_a_precropped_or_resized_source(tmp_path):
    portrait().save(tmp_path / "source.png")
    with pytest.raises(ValueError, match="original 2x"):
        heads.final_image(tmp_path, {"sourceFile": "source.png"})
