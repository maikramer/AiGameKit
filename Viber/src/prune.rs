//! Housekeeping cirúrgico do `target/` no arranque do `viber run`.
//!
//! Um checkout com dois perfis acumula dezenas de GB: cada executável de
//! teste do Bevy em dev leva ~2.2 GB de debug symbols e o cache incremental
//! acrescenta outros tantos. O `viber run` remove do perfil OPOSTO ao que vai
//! usar apenas o que é barato de reconstruir — executáveis (o cargo deteta a
//! falta do output e RE-LINKA em segundos, rlibs intactas) e caches
//! incrementais. Rlibs, fingerprints e outputs de build scripts ficam onde
//! estão: o próximo `cargo test` paga um relink, não uma recompilação.
//!
//! Guards (a limpeza nunca pode atrapalhar quem corre em paralelo):
//! `VIBER_PRUNE=0` desliga; com um cargo/rustc ALHEIO em curso salta-se
//! inteira (o cargo ancestral de `cargo run -- prune` é excluído do scan);
//! binários a correr de dentro do perfil a limpar são poupados.

use std::collections::HashSet;
use std::fs;
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};

/// Desliga o housekeeping por completo (simetria com `VIBER_HOT_RELOAD=0`).
const PRUNE_ENV_OFF: &str = "VIBER_PRUNE";

/// Resultado da limpeza — `describe()` devolve a linha de log (ou `None`
/// quando não há nada a reportar, para não poluir arranques normais).
pub struct PruneReport {
    bytes: u64,
    binaries: u64,
    incremental_cleared: bool,
    dropped_profile: &'static str,
    skipped: Option<&'static str>,
}

impl PruneReport {
    fn nothing() -> Self {
        PruneReport {
            bytes: 0,
            binaries: 0,
            incremental_cleared: false,
            dropped_profile: "",
            skipped: None,
        }
    }

    /// Uma linha de resumo, ou `None` se não houve limpeza nem motivo.
    pub fn describe(&self) -> Option<String> {
        if let Some(reason) = self.skipped {
            return Some(format!("saltado — {reason}"));
        }
        if self.bytes == 0 && !self.incremental_cleared {
            return None;
        }
        let mut parts = Vec::new();
        if self.binaries > 0 {
            parts.push(format!("{} executáveis de build/teste", self.binaries));
        }
        if self.incremental_cleared {
            parts.push("caches incrementais".to_string());
        }
        let gib = 1024.0 * 1024.0 * 1024.0;
        let size = if self.bytes >= gib as u64 {
            format!("{:.1} GB", self.bytes as f64 / gib)
        } else {
            format!("{:.0} MB", self.bytes as f64 / (gib / 1024.0))
        };
        Some(format!(
            "{size} libertados em target/{} ({}; rlibs/fingerprints mantidos \
             — cargo re-linka em segundos)",
            self.dropped_profile,
            parts.join(" + ")
        ))
    }
}

fn report_skipped(reason: &'static str) -> PruneReport {
    let mut report = PruneReport::nothing();
    report.skipped = Some(reason);
    report
}

/// Executa o housekeeping do checkout: `active_debug` declara o perfil que o
/// run VAI usar (fica intacto); o perfil oposto é que se limpa.
pub fn housekeeping(checkout_root: &Path, active_debug: bool) -> PruneReport {
    if std::env::var_os(PRUNE_ENV_OFF).is_some_and(|v| v == "0") {
        return report_skipped("VIBER_PRUNE=0");
    }
    let (dropped_profile, drop_dir) = if active_debug {
        ("release", checkout_root.join("target").join("release"))
    } else {
        ("debug", checkout_root.join("target").join("debug"))
    };
    if !drop_dir.is_dir() {
        return PruneReport::nothing();
    }

    let own_tree = own_process_tree();
    if any_foreign_build_running(&own_tree) {
        return report_skipped("cargo/rustc de outro processo em curso");
    }
    let live = live_exes_under(&drop_dir, &own_tree);

    let mut binaries = Vec::new();
    collect_binaries(&drop_dir, &mut binaries);
    let (bytes, removed) = remove_files(&binaries, &live);

    // Cache incremental: recursivamente grande, 100% reconstruível.
    let incremental = drop_dir.join("incremental");
    let inc_bytes = dir_size(&incremental);
    let incremental_cleared = incremental.is_dir() && fs::remove_dir_all(&incremental).is_ok();

    PruneReport {
        bytes: bytes + if incremental_cleared { inc_bytes } else { 0 },
        binaries: removed,
        incremental_cleared,
        dropped_profile,
        skipped: None,
    }
}

/// Ficheiros-executável órfãos do perfil: binário final na raiz (`viber`),
/// executáveis de teste em `deps/` (`hud_contract-<hash>`) e `examples/`.
/// Reconhecidos por serem regulares, executáveis e SEM extensão — rlibs
/// (`.rlib`), metadata (`.rmeta`), dependências (`.d`) e partilhadas (`.so`)
/// ficam de fora. `.fingerprint`/`build` não são tocados. Subdiretórios
/// ausentes (perfil sem `examples/`) contam como zero vítimas.
fn collect_binaries(profile_dir: &Path, out: &mut Vec<PathBuf>) {
    for dir in [profile_dir.to_path_buf(), profile_dir.join("deps"), profile_dir.join("examples")]
    {
        let Ok(entries) = fs::read_dir(&dir) else { continue };
        for entry in entries.flatten() {
            let Ok(metadata) = entry.metadata() else { continue };
            if !metadata.is_file() {
                continue;
            }
            let file_name = entry.file_name();
            let Some(name) = file_name.to_str() else { continue };
            if name.contains('.') {
                continue;
            }
            if metadata.permissions().mode() & 0o111 == 0 {
                continue;
            }
            out.push(entry.path());
        }
    }
}

/// Remove as vítimas (poupando binários em execução), contando bytes por
/// inode UMA vez — `deps/viber-<hash>` e `target/<perfil>/viber` são
/// hardlinks do mesmo ficheiro e o `du` já os contava uma única vez.
fn remove_files(paths: &[PathBuf], live: &HashSet<PathBuf>) -> (u64, u64) {
    let mut seen_inodes = HashSet::new();
    let (mut bytes, mut removed) = (0u64, 0u64);
    for path in paths {
        if live.contains(path) {
            continue;
        }
        let Ok(metadata) = fs::metadata(path) else { continue };
        let first_copy = seen_inodes.insert((metadata.dev(), metadata.ino()));
        if fs::remove_file(path).is_ok() {
            removed += 1;
            if first_copy {
                bytes += metadata.len();
            }
        }
    }
    (bytes, removed)
}

fn dir_size(path: &Path) -> u64 {
    let mut total = 0u64;
    let mut stack = vec![path.to_path_buf()];
    while let Some(current) = stack.pop() {
        let Ok(entries) = fs::read_dir(&current) else { continue };
        for entry in entries.flatten() {
            let Ok(metadata) = entry.metadata() else { continue };
            if metadata.is_dir() {
                stack.push(entry.path());
            } else {
                total += metadata.len();
            }
        }
    }
    total
}

/// Pids de nós e de todos os ancestrais — o cargo que nos lançou
/// (`cargo run -- prune`) está aqui e não conta como build alheio.
fn own_process_tree() -> HashSet<u32> {
    let mut tree = HashSet::new();
    let mut pid = std::process::id();
    loop {
        tree.insert(pid);
        let Ok(stat) = fs::read_to_string(format!("/proc/{pid}/stat")) else {
            break;
        };
        // O comm pode conter parênteses/espaços — o ppid é o 2.º campo após
        // o ÚLTIMO ')'.
        let Some(fields) = stat.rsplit(')').next() else {
            break;
        };
        let Some(ppid) = fields.split_whitespace().nth(1).and_then(|f| f.parse().ok()) else {
            break;
        };
        if ppid == 0 || ppid == pid {
            break;
        }
        pid = ppid;
    }
    tree
}

/// Algum cargo/rustc/sccache fora da nossa árvore de processos? O build em
/// curso pode estar a escrever exatamente nos ficheiros que iamos apagar.
fn any_foreign_build_running(skip: &HashSet<u32>) -> bool {
    let Ok(entries) = fs::read_dir("/proc") else {
        return false;
    };
    for entry in entries.flatten() {
        let Some(pid) = entry.file_name().to_str().and_then(|n| n.parse::<u32>().ok()) else {
            continue;
        };
        if skip.contains(&pid) {
            continue;
        }
        if let Ok(comm) = fs::read_to_string(format!("/proc/{pid}/comm")) {
            let comm = comm.trim();
            if matches!(comm, "cargo" | "rustc" | "sccache") {
                return true;
            }
        }
    }
    false
}

/// Caminhos de binários EM EXECUÇÃO que vivem dentro de `dir` — poupá-los
/// mantém o contrato "nunca tirar o chão a um processo vivo" (o unlink até
/// seria seguro no Linux, mas o run seguinte desse agente deve encontrar o
/// binário onde ele estava).
fn live_exes_under(dir: &Path, skip: &HashSet<u32>) -> HashSet<PathBuf> {
    let mut live = HashSet::new();
    let Ok(dir) = dir.canonicalize() else {
        return live;
    };
    let Ok(entries) = fs::read_dir("/proc") else {
        return live;
    };
    for entry in entries.flatten() {
        let Some(pid) = entry.file_name().to_str().and_then(|n| n.parse::<u32>().ok()) else {
            continue;
        };
        if skip.contains(&pid) {
            continue;
        }
        if let Ok(exe) = fs::read_link(format!("/proc/{pid}/exe")) {
            if exe.starts_with(&dir) {
                live.insert(exe);
            }
        }
    }
    live
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture_root(tag: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!("viber-prune-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("target/debug/deps")).unwrap();
        fs::create_dir_all(root.join("target/debug/.fingerprint/abc")).unwrap();
        fs::create_dir_all(root.join("target/debug/build/pkg/out")).unwrap();
        fs::create_dir_all(root.join("target/release/deps")).unwrap();
        root
    }

    fn write(path: &Path, bytes: &[u8], executable: bool) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(path, bytes).unwrap();
        let mut perms = fs::metadata(path).unwrap().permissions();
        perms.set_mode(if executable { 0o755 } else { 0o644 });
        fs::set_permissions(path, perms).unwrap();
    }

    #[test]
    fn selects_only_extensionless_executables() {
        let root = fixture_root("select");
        let debug = root.join("target/debug");
        write(&debug.join("viber"), b"x", true);
        write(&debug.join("viber.d"), b"x", false);
        write(&debug.join("libviber.rlib"), b"x", false);
        write(&debug.join("deps/hud_contract-6a30a0a1f7fb47f0"), b"x", true);
        write(&debug.join("deps/libviber-bbd8b3e311499d44.rlib"), b"x", false);
        write(&debug.join("deps/some.rmeta"), b"x", false);
        write(&debug.join("deps/notes.d"), b"x", false);
        write(&debug.join("examples/demo-1234"), b"x", true);
        write(&debug.join(".fingerprint/abc/dep-bin-abc"), b"x", true);
        write(&debug.join("build/pkg/out/script"), b"x", true);

        let mut found = Vec::new();
        collect_binaries(&debug, &mut found);
        let mut names: Vec<String> = found
            .iter()
            .map(|p| p.file_name().unwrap().to_string_lossy().into_owned())
            .collect();
        names.sort();
        assert_eq!(
            names,
            vec![
                "demo-1234".to_string(),
                "hud_contract-6a30a0a1f7fb47f0".to_string(),
                "viber".to_string(),
            ]
        );
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn prune_keeps_profile_libs_and_fingerprints() {
        let root = fixture_root("prune");
        let debug = root.join("target/debug");
        let release = root.join("target/release");
        write(&debug.join("viber"), &[7u8; 4096], true);
        write(&debug.join("deps/viber-fb9bf3c6459c9da0"), &[7u8; 8192], true);
        write(&debug.join("deps/libviber-bbd8b3e311499d44.rlib"), &[7u8; 512], false);
        write(&debug.join(".fingerprint/abc/f1"), b"keep", false);
        write(&debug.join("build/pkg/out/o.txt"), b"keep", false);
        write(&debug.join("incremental/viber-abc/session.d"), b"cache", false);
        write(&release.join("viber"), &[7u8; 2048], true);
        write(&release.join("deps/libviber-xyz.rlib"), &[7u8; 256], false);

        // As peças que `housekeeping` orquestra (os guards /proc não são
        // testáveis aqui — durante `cargo test` o próprio cargo está vivo).
        let live = HashSet::new();
        let mut binaries = Vec::new();
        collect_binaries(&debug, &mut binaries);
        let (bytes, removed) = remove_files(&binaries, &live);
        assert_eq!(removed, 2);
        assert_eq!(bytes, 4096 + 8192);
        assert!(fs::remove_dir_all(debug.join("incremental")).is_ok());

        // Perfil limpo: executáveis e incremental fora; libs e fingerprints dentro.
        assert!(!debug.join("viber").exists());
        assert!(!debug.join("deps/viber-fb9bf3c6459c9da0").exists());
        assert!(!debug.join("incremental").exists());
        assert!(debug.join("deps/libviber-bbd8b3e311499d44.rlib").exists());
        assert!(debug.join(".fingerprint/abc/f1").exists());
        assert!(debug.join("build/pkg/out/o.txt").exists());
        // O perfil oposto fica intocado.
        assert!(release.join("viber").exists());
        assert!(release.join("deps/libviber-xyz.rlib").exists());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn hardlinked_binaries_count_once_but_both_names_go() {
        let root = fixture_root("hardlink");
        let debug = root.join("target/debug");
        write(&debug.join("deps/viber-6a30a0a1f7fb47f0"), &[3u8; 100], true);
        fs::hard_link(
            debug.join("deps/viber-6a30a0a1f7fb47f0"),
            debug.join("viber"),
        )
        .unwrap();

        let mut binaries = Vec::new();
        collect_binaries(&debug, &mut binaries);
        let (bytes, removed) = remove_files(&binaries, &HashSet::new());
        assert_eq!(removed, 2, "ambos os nomes do inode são removidos");
        assert_eq!(bytes, 100, "bytes contados uma vez, como no du");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn live_binaries_are_spared() {
        let root = fixture_root("live");
        let debug = root.join("target/debug");
        write(&debug.join("viber"), &[5u8; 32], true);
        write(&debug.join("deps/test-1234"), &[5u8; 32], true);

        let mut live = HashSet::new();
        live.insert(debug.join("viber"));
        let mut binaries = Vec::new();
        collect_binaries(&debug, &mut binaries);
        let (_, removed) = remove_files(&binaries, &live);
        assert_eq!(removed, 1);
        assert!(debug.join("viber").exists(), "binário em execução fica");
        assert!(!debug.join("deps/test-1234").exists());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn process_tree_includes_ancestors() {
        let tree = own_process_tree();
        assert!(tree.contains(&std::process::id()));
        // O teste corre sob cargo (direta ou indiretamente) — a árvore tem de
        // subir até o excluir do guard de builds alheios.
        assert!(tree.len() >= 2);
    }
}
