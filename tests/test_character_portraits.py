"""Source identity and atomic publication checks using synthetic atlas output."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ui import character_portrait as portraits


def prepare(tmp_path, monkeypatch, sprites):
    source = tmp_path / "data.unity3d"
    source.write_bytes(b"synthetic player data")
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "characters.json").write_text(json.dumps({"characters": {
        "21": {"unitId": 21, "name": "original", "identity": {"gameCharacterId": 21}},
        "27": {"unitId": 27, "name": "variant", "identity": {"gameCharacterId": 21}},
    }}), encoding="utf-8")

    def decode(player_data, out_dir, **kwargs):
        assert kwargs["decode_atlases"] == (portraits.ATLAS,)
        assert kwargs["crop_names"] == ("chr_sp_21", "chr_sp_27")
        out = Path(out_dir)
        for row in sprites:
            if row.get("crop"):
                (out / row["crop"]).write_bytes(b"\x89PNG\r\n\x1a\n" + row["name"].encode())
        (out / "atlas.json").write_text(json.dumps({"atlases": [{
            "name": portraits.ATLAS, "file": "resources.assets", "pathId": 20,
            "sprites": sprites}]}), encoding="utf-8")
    monkeypatch.setattr(portraits, "extract_atlas", decode)
    return source, root


def run(source, root):
    return portraits.export_character_portraits(source, root, source_region="cn",
                                                source_game_version="1.0")


def test_variant_unit_ids_do_not_collapse_to_shared_game_character(tmp_path, monkeypatch):
    source, root = prepare(tmp_path, monkeypatch, [
        {"name": "chr_sp_21", "crop": "a.png", "sprite": {"pathId": 1}},
        {"name": "chr_sp_27", "crop": "b.png", "sprite": {"pathId": 2}},
    ])
    result = run(source, root)
    assert result["coverage"] == {"requested": 2, "exported": 2, "missing": []}
    assert [row["unitId"] for row in result["characters"]] == [21, 27]
    assert len({row["image"] for row in result["characters"]}) == 2
    assert all((root / row["image"]).is_file() for row in result["characters"])
    assert result["source"]["region"] == "cn"


def test_missing_source_is_reported_without_substituting_other_incarnation(tmp_path, monkeypatch):
    source, root = prepare(tmp_path, monkeypatch, [{"name": "chr_sp_21", "crop": "a.png"}])
    result = run(source, root)
    assert [row["unitId"] for row in result["characters"]] == [21]
    assert result["coverage"]["missing"][0]["unitId"] == 27


def test_ambiguous_sprite_preserves_previous_manifest(tmp_path, monkeypatch):
    source, root = prepare(tmp_path, monkeypatch, [
        {"name": "chr_sp_21", "crop": "a.png"}, {"name": "chr_sp_21", "crop": "b.png"}])
    previous = root / portraits.OUTPUT / "character-portraits.json"
    previous.parent.mkdir(parents=True)
    previous.write_text('{"previous":true}')
    with pytest.raises(ValueError, match="ambiguous"):
        run(source, root)
    assert previous.read_text() == '{"previous":true}'


def test_roster_key_must_agree_with_positive_unit_identity():
    with pytest.raises(ValueError, match="must agree"):
        portraits._roster({"characters": {"21": {"unitId": 27}}})
    with pytest.raises(ValueError, match="must agree"):
        portraits._roster({"characters": {"True": {"unitId": True}}})
