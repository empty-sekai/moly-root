"""Shader ABI lowering is independent of weather IDs and shader-family names."""
from pathlib import Path
import os
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from shaders.bridge import BridgeError, Compiler, prepare
from shaders.programs import digest


def source(*, sampler="sampler2D", uv="vec2", extra_vertex="", extra_fragment=""):
    return f"""#ifdef VERTEX
#version 300 es
uniform highp vec4 matrix[4];
uniform highp float shared;
uniform highp float unused;
in highp vec4 in_POSITION0;
in highp {uv} in_TEXCOORD0;
out highp {uv} vs_TEXCOORD0;
{extra_vertex}
void main()
{{
 gl_Position=matrix[0]*in_POSITION0.x+matrix[1]*in_POSITION0.y+matrix[2]*in_POSITION0.z+matrix[3];
 vs_TEXCOORD0=in_TEXCOORD0*shared;
}}
#endif
#ifdef FRAGMENT
#version 300 es
uniform highp {sampler} image;
uniform highp float shared;
in highp {uv} vs_TEXCOORD0;
layout(location = 0) out mediump vec4 SV_Target0;
{extra_fragment}
void main()
{{
 SV_Target0=texture(image,vs_TEXCOORD0)*shared;
}}
#endif
""".encode()


def prepared(**kwargs):
    data = source(**kwargs)
    return prepare(data, digest(data))


def test_uniform_union_has_exact_stable_slot_offsets_and_no_default_values():
    result = prepared()
    assert result.abi["uniformBytes"] == 80
    assert [(u["name"], u["offset"], u["bytes"]) for u in result.abi["uniforms"]] == [("matrix", 0, 64), ("shared", 64, 16)]
    assert result.abi["uniforms"][1]["stages"] == ["vertex", "fragment"]
    assert result.abi["unusedUniforms"]["vertex"][0]["name"] == "unused"
    assert result.abi["sourceClipSpace"] == "gl-minus-one-to-one"
    assert "in_POSITION0=vec4(moly_position,1.0)" in result.stages["vertex"]


def test_texture_and_sampler_have_distinct_bindings_and_source_uv_is_unchanged():
    result = prepared()
    t = result.abi["textures"][0]
    assert (t["textureBinding"], t["samplerBinding"], t["dimension"]) == (1, 2, "2d")
    assert "return texture(sampler2D(moly_texture_0,moly_sampler_0),uv);" in result.stages["fragment"]
    assert "1.0-uv.y" not in result.stages["fragment"]
    assert result.abi["textureDataOrigin"] == "unity-lower-left"


def test_texture_array_is_not_flattened_and_gl_layer_rounding_is_explicit():
    result = prepared(sampler="sampler2DArray", uv="vec3")
    assert result.abi["textures"][0]["dimension"] == "2d-array"
    assert "floor(uv.z+0.5)" in result.stages["fragment"]
    assert "textureSize(sampler2DArray" in result.stages["fragment"]


def test_source_hash_is_checked_before_parsing():
    with pytest.raises(BridgeError, match="SHA-256"):
        prepare(source() + b" ", digest(source()))


def test_unknown_texture_dimensions_fail_instead_of_becoming_2d():
    with pytest.raises(BridgeError, match="texture dimension"):
        prepared(sampler="sampler3D", uv="vec3")


def test_cross_stage_type_conflict_fails_before_compilation():
    data = source().replace(b"uniform highp float shared;", b"uniform highp vec2 shared;", 1)
    with pytest.raises(BridgeError, match="cross-stage uniform type conflict"):
        prepare(data, digest(data))


def test_unknown_vertex_semantic_is_not_assigned_an_arbitrary_location():
    with pytest.raises(BridgeError, match="unmapped source interface"):
        prepared(extra_vertex="in highp vec4 in_UNKNOWN0;")


def test_nonunique_stage_and_unterminated_stage_fail_explicitly():
    for data, match in [(source() + b"\n#ifdef VERTEX\n#endif", "expected one"),
                        (source().rsplit(b"#endif", 1)[0], "unterminated")]:
        with pytest.raises(BridgeError, match=match):
            prepare(data, digest(data))


def test_matrix_types_are_not_silently_packed_as_vectors():
    with pytest.raises(BridgeError, match="unsupported uniform type"):
        prepared(extra_vertex="uniform mat4 unsupported;\n#define matrix unsupported")


def test_real_offline_compiler_validates_both_stages_and_reflects_array_layout():
    executable = os.environ.get("MOLY_SHADER_BRIDGE")
    if not executable:
        pytest.skip("supply the built offline compiler through MOLY_SHADER_BRIDGE")
    compiler = Compiler(executable)
    for options in ({}, {"sampler": "sampler2DArray", "uv": "vec3"}):
        result = compiler.compile(prepared(**options))
        assert result["compiler"] == "naga 27.0.3"
        assert len(result["stages"]) == 2
        assert all(s["roundTripValidated"] for s in result["stages"])
        assert len(result["executableSha256"]) == 64
