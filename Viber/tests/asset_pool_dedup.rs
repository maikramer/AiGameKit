//! Guarda anti-regressão da política de assets (docs/ASSETS.md): o pool
//! partilhado `examples/shared-assets/public/assets` é a ÚNICA cópia dos
//! assets partilhados.
//!
//! A duplicação já foi corrigida e reintroduzida antes: o plugin
//! `sharedAssets` do VibeGame (2026-08-25, "um pool, sem symlinks nem cópias
//! por exemplo") eliminou o distribuidor por exemplo; o Viber reintroduziu
//! um espelho por mundo (`sync_assets.py`) enquanto o Bevy não lia meshopt —
//! e entretanto assets vivos chegaram a ser commitados DENTRO do espelho
//! (2026-09-01, revertido em 2026-09-05). Desde que o `MeshoptAssetReader`
//! decodifica EXT_meshopt à leitura, a engine serve o pool diretamente e
//! qualquer cópia por exemplo é regressão. Este teste parte a build de quem
//! a reintroduzir — por script ressuscitado ou cópia manual "só para
//! testar".
//!
//! Regra: um ficheiro sob `examples/*/assets/` cujo caminho relativo também
//! existe no pool é um *shadow*. Shadows só são legítimos como override
//! deliberado por-mundo (o ficheiro local ganha na resolução) e cada um tem
//! de estar na allowlist abaixo com um comentário a justificar.

use std::path::Path;

/// Overrides deliberados, na forma `<exemplo>/assets/<caminho relativo>`.
/// A lista começa vazia por princípio — acrescentar exige justificação.
const ALLOWED_OVERRIDES: &[&str] = &[];

#[test]
fn examples_may_not_shadow_shared_pool_assets() {
    let Some(pool) = viber::meshopt::shared_asset_pool() else {
        eprintln!("skip: pool partilhado sem binários neste checkout");
        return;
    };
    let pool_assets = pool.join("assets");
    let examples = Path::new(env!("CARGO_MANIFEST_DIR")).join("examples");

    let mut shadows: Vec<String> = Vec::new();
    for entry in std::fs::read_dir(&examples).expect("examples dir") {
        let entry = entry.expect("entry");
        // O próprio pool não é um exemplo (e os seus ficheiros são a fonte,
        // não cópias).
        if entry.file_name().to_string_lossy() == "shared-assets" {
            continue;
        }
        let assets = entry.path().join("assets");
        if !assets.is_dir() {
            continue;
        }
        let example = entry.file_name().to_string_lossy().into_owned();
        collect_shadows(&assets, &assets, &pool_assets, &mut |rel| {
            let full = format!("{example}/assets/{}", rel.to_string_lossy());
            if !ALLOWED_OVERRIDES.contains(&full.as_str()) {
                shadows.push(full);
            }
        });
    }
    assert!(
        shadows.is_empty(),
        "assets que duplicam o pool partilhado — o pool é a única cópia \
         (docs/ASSETS.md); um override por-mundo tem de entrar na allowlist \
         deste teste com justificação:\n  {}",
        shadows.join("\n  ")
    );
}

/// `worlds/assets` já foi um symlink para o pool — banido: a engine resolve
/// o pool por fallback (docs/ASSETS.md) e symlinks quebram checkouts e
/// ferramentas que não os seguem.
#[test]
fn worlds_assets_symlink_stays_dead() {
    let worlds_assets = Path::new(env!("CARGO_MANIFEST_DIR")).join("worlds/assets");
    let meta = std::fs::symlink_metadata(&worlds_assets);
    assert!(
        meta.is_err(),
        "worlds/assets voltou a existir ({:?}) — a engine serve o pool por \
         fallback, symlinks estão banidos (docs/ASSETS.md)",
        meta.map(|m| m.file_type())
    );
}

fn collect_shadows(root: &Path, dir: &Path, pool_assets: &Path, emit: &mut dyn FnMut(&Path)) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_shadows(root, &path, pool_assets, emit);
        } else if let Ok(rel) = path.strip_prefix(root) {
            if pool_assets.join(rel).exists() {
                emit(rel);
            }
        }
    }
}
