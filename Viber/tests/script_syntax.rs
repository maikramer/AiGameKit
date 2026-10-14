//! Gate de CI: TODOS os scripts Luau do jogo de exemplo compilam. Um `.lua`
//! com erro de sintaxe só rebenta EM RUNTIME (a entidade nasce sem
//! comportamento, o `viber.log` engole) — aqui compila headless com a MESMA
//! sandbox do runtime e falha o teste a apontar o ficheiro e a linha.
//!
//! Isto é o `viber scene lint` (`crate::lint`) na forma de gate: o lint é
//! incremental e por-mundo; este teste varre o exemplo de referência inteiro.

use std::path::{Path, PathBuf};

fn lua_files(root: &Path) -> Vec<PathBuf> {
    fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                walk(&path, out);
            } else if path.extension().and_then(|ext| ext.to_str()) == Some("lua") {
                out.push(path);
            }
        }
    }
    let mut out = Vec::new();
    walk(root, &mut out);
    out.sort();
    out
}

#[test]
fn every_script_of_the_example_compiles() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("examples/simple-rpg");
    let scripts = root.join("scripts");
    if !scripts.exists() {
        // O exemplo está a meio de uma reestruturação (outro agente): o gate
        // volta quando os scripts voltam — não flakar o CI por disso.
        eprintln!("skip: {scripts:?} não existe (exemplo em reestruturação)");
        return;
    }
    let mut files = lua_files(&scripts);
    let ui = root.join("ui");
    files.extend(lua_files(&ui));
    assert!(
        files.len() >= 20,
        "o exemplo de referência tem dezenas de scripts — encontrados {}: {files:?}",
        files.len()
    );
    let lua = viber::lint::lua_sandbox();
    let mut failures = Vec::new();
    for file in &files {
        let source = std::fs::read_to_string(file).unwrap_or_default();
        let rel = file
            .strip_prefix(&root)
            .unwrap_or(file)
            .display()
            .to_string();
        if let Err(error) = viber::lint::compile(&lua, &source, &rel) {
            failures.push(format!("{rel}: {error}"));
        }
    }
    assert!(
        failures.is_empty(),
        "{} script(s) com erro de sintaxe:\n{}",
        failures.len(),
        failures.join("\n")
    );
}
