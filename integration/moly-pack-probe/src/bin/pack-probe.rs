#[cfg(not(target_arch = "wasm32"))]
fn main() {
    let run = || -> Result<(), Box<dyn std::error::Error>> {
        let mut args = std::env::args().skip(1);
        let root = args
            .next()
            .ok_or("usage: pack-probe STORE CATALOG_SHA_OR_DASH PATHS_JSON")?;
        let catalog = args.next().ok_or("missing catalog")?;
        let paths: Vec<String> =
            serde_json::from_slice(&std::fs::read(args.next().ok_or("missing paths JSON")?)?)?;
        let result = futures_lite::future::block_on(moly_pack_probe::native_probe(
            root,
            (catalog != "-").then_some(catalog),
            paths,
        ))?;
        println!("{}", serde_json::to_string_pretty(&result)?);
        Ok(())
    };
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
#[cfg(target_arch = "wasm32")]
fn main() {}
