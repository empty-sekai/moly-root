//! Offline GLES binding-ABI compiler. No game assets or runtime dependencies.
//!
//! One JSON request on stdin, one complete receipt on stdout. Nothing is
//! published when a stage fails. The caller owns content-addressed storage.
mod clip;

use naga::{back, front, valid, ShaderStage, TypeInner};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::{Read, Write};

const MAX_REQUEST_BYTES: u64 = 16 * 1024 * 1024;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Request {
    schema_version: u32,
    stages: Vec<Stage>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Stage {
    stage: String,
    source: String,
    source_sha256: String,
}

fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn type_description(module: &naga::Module, handle: naga::Handle<naga::Type>) -> Value {
    match &module.types[handle].inner {
        TypeInner::Scalar(scalar) => {
            json!({"kind":"scalar", "scalar":format!("{:?}", scalar.kind), "width":scalar.width})
        }
        TypeInner::Vector { size, scalar } => {
            json!({"kind":"vector", "components":*size as u8, "scalar":format!("{:?}", scalar.kind), "width":scalar.width})
        }
        TypeInner::Matrix {
            columns,
            rows,
            scalar,
        } => {
            json!({"kind":"matrix", "columns":*columns as u8, "rows":*rows as u8, "scalar":format!("{:?}", scalar.kind), "width":scalar.width})
        }
        TypeInner::Array { base, size, stride } => {
            json!({"kind":"array", "element":type_description(module,*base), "count":match size {naga::ArraySize::Constant(n)=>Some(n.get()), _=>None}, "stride":stride})
        }
        TypeInner::Struct { members, span } => {
            json!({"kind":"struct", "bytes":span, "fields":members.iter().map(|member|json!({"name":member.name,"offset":member.offset,"type":type_description(module,member.ty)})).collect::<Vec<_>>()})
        }
        TypeInner::Image {
            dim,
            arrayed,
            class,
        } => {
            json!({"kind":"texture", "dimension":format!("{dim:?}"),"array":arrayed,"class":format!("{class:?}")})
        }
        TypeInner::Sampler { comparison } => json!({"kind":"sampler", "comparison":comparison}),
        other => json!({"kind":"unsupported", "description":format!("{other:?}")}),
    }
}

fn reflect(module: &naga::Module) -> Vec<Value> {
    let mut result = Vec::new();
    for (_, global) in module.global_variables.iter() {
        let Some(binding) = &global.binding else {
            continue;
        };
        let mut value = type_description(module, global.ty);
        let object = value
            .as_object_mut()
            .expect("type descriptions are objects");
        object.insert("name".into(), json!(global.name));
        object.insert("group".into(), json!(binding.group));
        object.insert("binding".into(), json!(binding.binding));
        object.insert("addressSpace".into(), json!(format!("{:?}", global.space)));
        result.push(value);
    }
    result.sort_by_key(|value| (value["group"].as_u64(), value["binding"].as_u64()));
    result
}

fn binding_layout(mut value: Value) -> Value {
    match &mut value {
        Value::Object(fields) => {
            fields.remove("name");
            for item in fields.values_mut() {
                *item = binding_layout(item.take());
            }
        }
        Value::Array(items) => {
            for item in items {
                *item = binding_layout(item.take());
            }
        }
        _ => {}
    }
    value
}

fn compile(stage: &Stage) -> Result<Value, String> {
    if sha256(stage.source.as_bytes()) != stage.source_sha256 {
        return Err("stage source SHA-256 mismatch".into());
    }
    let kind = match stage.stage.as_str() {
        "vertex" => ShaderStage::Vertex,
        "fragment" => ShaderStage::Fragment,
        other => return Err(format!("unsupported shader stage {other}")),
    };
    let mut options = front::glsl::Options::from(kind);
    options
        .defines
        .insert(stage.stage.to_uppercase(), "1".into());
    let module = front::glsl::Frontend::default()
        .parse(&options, &stage.source)
        .map_err(|err| err.emit_to_string(&stage.source))?;
    let mut validator =
        valid::Validator::new(valid::ValidationFlags::all(), valid::Capabilities::all());
    let info = validator
        .validate(&module)
        .map_err(|err| format!("GLSL IR validation: {err:?}"))?;
    let wgsl = back::wgsl::write_string(&module, &info, back::wgsl::WriterFlags::EXPLICIT_TYPES)
        .map_err(|err| format!("WGSL generation: {err:?}"))?;
    let roundtrip = front::wgsl::parse_str(&wgsl).map_err(|err| err.emit_to_string(&wgsl))?;
    let roundtrip_info = validator
        .validate(&roundtrip)
        .map_err(|err| format!("WGSL roundtrip validation: {err:?}"))?;
    // Naga legally renames identifiers ending in digits (for example
    // moly_slot_0 -> moly_slot_0_). Byte offsets, scalar types, array strides,
    // binding addresses and texture dimensions must remain exactly equal.
    // Keep both symbol tables in the receipt rather than guessing a rename.
    if binding_layout(json!(reflect(&module))) != binding_layout(json!(reflect(&roundtrip))) {
        return Err("WGSL roundtrip changed the reflected resource ABI".into());
    }
    let mut glsl = String::new();
    let glsl_options = back::glsl::Options {
        version: back::glsl::Version::Embedded {
            version: 300,
            is_webgl: true,
        },
        // Coordinate adjustment belongs to the renderer adapter, not the
        // source arithmetic roundtrip. The receipt names that convention.
        writer_flags: back::glsl::WriterFlags::empty(),
        ..Default::default()
    };
    let pipeline = back::glsl::PipelineOptions {
        shader_stage: kind,
        entry_point: "main".into(),
        multiview: None,
    };
    let glsl_reflection = back::glsl::Writer::new(
        &mut glsl,
        &roundtrip,
        &roundtrip_info,
        &glsl_options,
        &pipeline,
        naga::proc::BoundsCheckPolicies::default(),
    )
    .map_err(|err| format!("WebGL writer: {err:?}"))?
    .write()
    .map_err(|err| format!("WebGL generation: {err:?}"))?;
    let mut textures: Vec<_> = glsl_reflection
        .texture_mapping
        .iter()
        .map(|(name, mapping)| {
            let image = &roundtrip.global_variables[mapping.texture];
            let sampler = mapping
                .sampler
                .and_then(|h| roundtrip.global_variables[h].binding.as_ref());
            json!({"name":name, "textureGroup":image.binding.as_ref().map(|b|b.group),
               "textureBinding":image.binding.as_ref().map(|b|b.binding),
               "samplerGroup":sampler.map(|b|b.group), "samplerBinding":sampler.map(|b|b.binding)})
        })
        .collect();
    textures.sort_by(|a, b| a["name"].as_str().cmp(&b["name"].as_str()));
    let mut uniforms: Vec<_> = glsl_reflection.uniforms.iter().map(|(handle,name)| {
        let binding = roundtrip.global_variables[*handle].binding.as_ref();
        json!({"name":name, "group":binding.map(|b|b.group), "binding":binding.map(|b|b.binding)})
    }).collect();
    uniforms.sort_by(|a, b| a["name"].as_str().cmp(&b["name"].as_str()));
    let renderer = if kind == ShaderStage::Vertex {
        let (adapted, returns) = clip::reverse_zero_to_one(&roundtrip)?;
        let adapted_info = validator.validate(&adapted).map_err(|e| format!("renderer clip adapter validation: {e:?}"))?;
        let adapted_wgsl = back::wgsl::write_string(&adapted, &adapted_info, back::wgsl::WriterFlags::EXPLICIT_TYPES)
            .map_err(|e| format!("renderer WGSL generation: {e:?}"))?;
        let check = front::wgsl::parse_str(&adapted_wgsl).map_err(|e| e.emit_to_string(&adapted_wgsl))?;
        validator.validate(&check).map_err(|e| format!("renderer WGSL revalidation: {e:?}"))?;
        if binding_layout(json!(reflect(&roundtrip))) != binding_layout(json!(reflect(&check))) {
            return Err("renderer clip adapter changed resource bindings".into());
        }
        json!({"schemaVersion":1,"sourceClipSpace":"gl-minus-one-to-one",
            "targetClipSpace":"reverse-zero-to-one","operation":"z=(w-z)*0.5",
            "adaptedReturns":returns,"entryPoint":"main","validated":true,
            "bindings":reflect(&check),"wgslSha256":sha256(adapted_wgsl.as_bytes()),"wgsl":adapted_wgsl})
    } else { Value::Null };
    Ok(json!({
        "stage": stage.stage, "entryPoint":"main", "sourceSha256":stage.source_sha256,
        "wgslSha256":sha256(wgsl.as_bytes()), "wgsl":wgsl,
        "roundtripGlslSha256":sha256(glsl.as_bytes()), "roundtripGlsl":glsl,
        "roundTripValidated":true, "sourceBindings":reflect(&module), "bindings":reflect(&roundtrip),
        "webglReflection":{"textures":textures,"uniforms":uniforms},
        "renderer":renderer,
    }))
}

fn execute(request: Request) -> Result<Value, String> {
    if request.schema_version != 1
        || request.stages.len() != 2
        || request
            .stages
            .iter()
            .filter(|s| s.stage == "vertex")
            .count()
            != 1
        || request
            .stages
            .iter()
            .filter(|s| s.stage == "fragment")
            .count()
            != 1
    {
        return Err("request must contain exactly one vertex and one fragment stage".into());
    }
    let stages = request
        .stages
        .iter()
        .map(compile)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(json!({"schemaVersion":1, "compiler":"naga 27.0.3",
        "validation":"all", "capabilities":"all; target device limits require runtime validation",
        "glslVersion":"webgl-es-300", "clipSpace":"source-gl-minus-one-to-one",
        "stages":stages}))
}

fn run() -> Result<(), String> {
    let mut bytes = Vec::new();
    std::io::stdin()
        .take(MAX_REQUEST_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|err| format!("read compiler request: {err}"))?;
    if bytes.len() as u64 > MAX_REQUEST_BYTES {
        return Err("compiler request exceeds 16 MiB".into());
    }
    let request =
        serde_json::from_slice(&bytes).map_err(|err| format!("invalid compiler request: {err}"))?;
    let result = execute(request)?;
    let output = serde_json::to_vec(&result).map_err(|err| err.to_string())?;
    std::io::stdout()
        .write_all(&output)
        .map_err(|err| err.to_string())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn stage(name: &str, body: &str) -> Stage {
        Stage {
            stage: name.into(),
            source: body.into(),
            source_sha256: sha256(body.as_bytes()),
        }
    }
    #[test]
    fn duplicate_stages_are_rejected_before_compilation() {
        let request = Request {
            schema_version: 1,
            stages: vec![stage("vertex", ""), stage("vertex", "")],
        };
        assert!(execute(request).unwrap_err().contains("exactly one"));
    }
    #[test]
    fn symbol_names_are_not_byte_layout_but_offsets_are() {
        let a = json!({"name":"a", "fields":[{"name":"member_0","offset":0,"type":{"kind":"vector","components":4,"width":4}}]});
        let mut b = a.clone();
        b["name"] = json!("renamed");
        b["fields"][0]["name"] = json!("member_0_");
        assert_eq!(binding_layout(a.clone()), binding_layout(b.clone()));
        b["fields"][0]["offset"] = json!(16);
        assert_ne!(binding_layout(a.clone()), binding_layout(b.clone()));
        b["fields"][0]["offset"] = json!(0);
        b["fields"][0]["type"]["width"] = json!(2);
        assert_ne!(binding_layout(a), binding_layout(b));
    }
    #[test]
    fn source_integrity_is_checked_independently() {
        let mut request = stage("vertex", "invalid but correctly hashed");
        request.source.push('!');
        assert!(compile(&request).unwrap_err().contains("SHA-256"));
    }
    #[test]
    fn unsupported_stage_does_not_get_a_vertex_default() {
        assert!(compile(&stage("compute", ""))
            .unwrap_err()
            .contains("unsupported shader stage"));
    }
    #[test]
    fn reflected_uniform_layout_survives_roundtrip() {
        let result = compile(&stage("vertex", "#version 450\nlayout(set=0,binding=0,std140) uniform Inputs {vec4 a;uvec4 b;vec4 m[4];} u;\nlayout(location=0) in vec3 p;\nvoid main(){gl_Position=vec4(p,1.0)*u.a+u.m[int(u.b.x)];}\n")).unwrap();
        let block = &result["bindings"][0];
        assert_eq!(block["bytes"], 96);
        assert_eq!(block["fields"][0]["offset"], 0);
        assert_eq!(block["fields"][1]["offset"], 16);
        assert_eq!(block["fields"][2]["offset"], 32);
        assert_eq!(result["roundTripValidated"], true);
    }
}
