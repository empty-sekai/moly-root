// Compile the consumer modules supplied by the caller.
use std::{env, fs, path::PathBuf};
fn main() {
    let root = env::var_os("MOLY_CONSUMER_WORKSPACE")
        .map(PathBuf::from)
        .expect("set MOLY_CONSUMER_WORKSPACE to a Moly source checkout");
    let source = root
        .join("crates/moly-assets/src")
        .canonicalize()
        .expect("MOLY_CONSUMER_WORKSPACE must contain crates/moly-assets/src");
    let mut code = String::new();
    for name in ["packs", "read_limits", "http", "http_path"] {
        let file = source.join(format!("{name}.rs"));
        println!("cargo:rerun-if-changed={}", file.display());
        if name == "http" {
            code.push_str("#[cfg(target_arch = \"wasm32\")]\n");
        }
        if name == "http_path" {
            code.push_str("#[cfg(any(target_arch = \"wasm32\", test))]\n");
        }
        code.push_str(&format!(
            "#[path = {:?}] mod {};\n",
            file.to_str().unwrap(),
            name
        ));
    }
    println!(
        "cargo:rerun-if-changed={}",
        source.join("packs/tests.rs").display()
    );
    println!("cargo:rerun-if-env-changed=MOLY_CONSUMER_WORKSPACE");
    fs::write(
        PathBuf::from(env::var_os("OUT_DIR").unwrap()).join("consumer_modules.rs"),
        code,
    )
    .unwrap();
}
