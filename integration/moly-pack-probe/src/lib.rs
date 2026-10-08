//! Executable cross-language conformance harness for the actual PackReader.
include!(concat!(env!("OUT_DIR"), "/consumer_modules.rs"));
use bevy::asset::io::AssetReader;
use bevy::asset::io::Reader;
#[cfg(not(target_arch = "wasm32"))]
use sha2::{Digest, Sha256};

async fn consume(reader: &packs::PackReader, logical: &str) -> Result<Vec<u8>, String> {
    let path = bevy::asset::AssetPath::from(format!("moly://{logical}"));
    let mut input = reader.read(path.path()).await.map_err(|e| e.to_string())?;
    let mut bytes = Vec::new();
    input
        .read_to_end(&mut bytes)
        .await
        .map_err(|e| e.to_string())?;
    Ok(bytes)
}

#[cfg(not(target_arch = "wasm32"))]
pub async fn native_probe(
    root: String,
    catalog: Option<String>,
    paths: Vec<String>,
) -> Result<serde_json::Value, String> {
    bevy::tasks::IoTaskPool::get_or_init(bevy::tasks::TaskPool::new);
    let mut source = bevy::asset::io::AssetSource::get_default_reader(root);
    let reader = packs::PackReader::new(source()).with_catalog(catalog);
    let mut result = Vec::new();
    for path in paths {
        let bytes = consume(&reader, &path).await?;
        result.push(serde_json::json!({"path":path,"bytes":bytes.len(),"sha256":format!("{:x}",Sha256::digest(&bytes))}));
    }
    Ok(serde_json::Value::Array(result))
}

#[cfg(target_arch = "wasm32")]
#[wasm_bindgen::prelude::wasm_bindgen]
pub async fn read_packed(
    root: String,
    catalog: String,
    path: String,
) -> Result<js_sys::Uint8Array, wasm_bindgen::JsValue> {
    let reader =
        packs::PackReader::http(root).with_catalog((!catalog.is_empty()).then_some(catalog));
    let bytes = consume(&reader, &path)
        .await
        .map_err(|e| wasm_bindgen::JsValue::from_str(&e))?;
    Ok(js_sys::Uint8Array::from(bytes.as_slice()))
}

#[cfg(target_arch = "wasm32")]
#[wasm_bindgen::prelude::wasm_bindgen]
extern "C" {
    #[wasm_bindgen::prelude::wasm_bindgen(js_namespace = console, js_name = error)]
    fn report_probe_panic(message: &str);
}
#[cfg(target_arch = "wasm32")]
#[wasm_bindgen::prelude::wasm_bindgen(start)]
pub fn install_probe_panic_reporter() {
    std::panic::set_hook(Box::new(|info| report_probe_panic(&info.to_string())));
}
