//! Hot-reload de scripts Luau — o watcher de ficheiros que reinjecta chunks
//! editados NA engine viva (item da fila aberta do roadmap).
//!
//! - Um [`notify::RecommendedWatcher`] recursivo sobre `<world>/scripts/`
//!   (cobre os scripts de jogo E os de UI — partilham o [`LuaScriptHost`]).
//! - Eventos caem num canal mpsc; o sistema [`hot_reload_poll`] drena-o a
//!   cada frame e aplica recargas após um quiet-period ([`DEBOUNCE`]) — os
//!   editores escrevem os ficheiros em vários passos (truncate+write+rename).
//! - Recarga = recompilar o chunk com env NOVA e re-correr o top-level nas
//!   entidades ativas. Erro de compilação → chunk antigo mantém-se (a engine
//!   NUNCA morre por um ficheiro a meio de ser escrito).
//! - Globals do chunk resetam (é um reload de página, não um diff-mescla);
//!   o estado por entidade em `viber.state()` sobrevive — vive fora do env.
//!
//! Gate: ON por defeito, `VIBER_HOT_RELOAD=0` desliga.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::mpsc::Receiver;
use std::time::{Duration, Instant};

use bevy::prelude::*;

use crate::luau::{LuaScriptHost, LuaScriptRef};

/// Quiet-period mínimo entre o último evento de um ficheiro e a recarga.
const DEBOUNCE: Duration = Duration::from_millis(250);

/// Um evento do watcher que representa ESCRITA (o único que deve recarregar).
///
/// `Access` fica de fora de propósito: a própria recarga abre e lê o ficheiro,
/// e num inotify com acessos ligados isso realimenta o watcher — a recarga
/// dispara a recarga seguinte. `Any`/`Other` contam como escrita (é o que os
/// backends que não classificam emitem; o debounce trata do resto).
pub fn is_write_event(kind: &notify::EventKind) -> bool {
    use notify::EventKind;
    match kind {
        EventKind::Access(_) => false,
        EventKind::Modify(_) | EventKind::Create(_) | EventKind::Remove(_) => true,
        EventKind::Any | EventKind::Other => true,
    }
}

/// Estado do watcher — recurso só inserido quando o hot-reload está ON e o
/// watcher arrancou com sucesso.
#[derive(Resource)]
pub struct HotReloadState {
    /// Raiz vigiada em forma canónica — os eventos do watcher chegam com
    /// ESTE prefixo, e o registry indexa caminhos relativos a ela.
    root: PathBuf,
    /// Caminho absoluto (como chega do watcher) → instant do último evento.
    pending: HashMap<PathBuf, Instant>,
    /// `Receiver` não é `Sync` (o Resource do Bevy exige) — o lock é
    /// disputado por um só sistema, é livre na prática.
    rx: std::sync::Mutex<Receiver<notify::Event>>,
    /// Mantém o watcher vivo (drop = desregistra as watches).
    _watcher: notify::RecommendedWatcher,
}

impl HotReloadState {
    /// Arranca o watcher recursivo sobre `scripts_dir`.
    pub fn new(scripts_dir: &Path) -> Result<Self, notify::Error> {
        let root = scripts_dir
            .canonicalize()
            .unwrap_or_else(|_| scripts_dir.to_path_buf());
        let (tx, rx) = std::sync::mpsc::channel();
        let mut watcher = notify::recommended_watcher(move |res: Result<notify::Event, _>| {
            if let Ok(event) = res {
                // SÓ escritas. O inotify também emite ACESSO, e a recarga LÊ o
                // ficheiro (`reload_script`) — o evento de leitura voltava
                // como "o ficheiro mudou" e fechava o ciclo: no `simple-rpg`
                // os ~38 scripts recarregavam 3×/s PARA SEMPRE, sem ninguém
                // editar nada (diagnosticado 2026-09-12; o log da sessão era
                // uma tempestade de `hot-reload: '…' recarregado`).
                if !is_write_event(&event.kind) {
                    return;
                }
                // Canal cheio = editor a fazer spam; a próxima recarga apanha
                // o ficheiro final. Nunca bloquear a thread do watcher.
                let _ = tx.send(event);
            }
        })?;
        use notify::Watcher;
        watcher.watch(&root, notify::RecursiveMode::Recursive)?;
        Ok(Self {
            root,
            pending: HashMap::new(),
            rx: std::sync::Mutex::new(rx),
            _watcher: watcher,
        })
    }

    /// Drena eventos, devolve os caminhos `.lua` cujo quiet-period expirou.
    fn ready_paths(&mut self) -> Vec<PathBuf> {
        let now = Instant::now();
        let Ok(rx) = self.rx.lock() else {
            return Vec::new();
        };
        while let Ok(event) = rx.try_recv() {
            for path in event.paths {
                if path.extension().is_some_and(|e| e == "lua") {
                    self.pending.insert(path, now);
                }
            }
        }
        let ready: Vec<PathBuf> = self
            .pending
            .iter()
            .filter(|(_, t)| now.duration_since(**t) >= DEBOUNCE)
            .map(|(p, _)| p.clone())
            .collect();
        for path in &ready {
            self.pending.remove(path);
        }
        ready
    }
}

/// Recarrega `rel` no host a partir do disco e re-corre o top-level nas
/// `entities` ativas (`(Entity, origin)` — a origem alimenta o `home` só na
/// primeira ativação; reloads preservam o home existente). Devolve quantas
/// entidades re-ativaram. Erro de LEITURA/compilação → `Err` e o chunk
/// antigo fica intacto (o `load_script` só substitui depois de compilar).
/// Erro no TOP-LEVEL (o chunk compila mas rebenta a correr) → `Err` e o chunk
/// antigo é reposto: sem isso a entidade ficava sem `on_update`, em silêncio.
/// Os timers do chunk antigo saem com a recarga — o top-level novo re-regista
/// os seus (senão cada save somava mais um `viber.every`).
pub fn reload_script(
    host: &mut LuaScriptHost,
    scripts_dir: &Path,
    rel: &str,
    entities: &[(Entity, Vec3)],
) -> Result<usize, String> {
    let full = scripts_dir.join(rel);
    let code =
        std::fs::read_to_string(&full).map_err(|e| format!("a ler {}: {e}", full.display()))?;
    let previous = host.registry.get(rel).cloned();
    let old_timers = crate::luau::timers::timer_ids_where(&host.lua, |_, path| path == rel);
    host.load_script(rel, &code)
        .map_err(|e| format!("a compilar '{rel}': {e}"))?;
    host.clear_warnings(rel);
    let mut count = 0;
    let mut first_err = None;
    for (entity, origin) in entities {
        // Top-level re-corre (o reload pôs `ran = false`). Um erro aqui não
        // desiste das restantes entidades — cada uma é independente.
        match host.activate_at(*entity, rel, *origin) {
            Ok(()) => count += 1,
            Err(e) => {
                first_err.get_or_insert_with(|| e.to_string());
            }
        }
    }
    if count == 0 {
        if let Some(err) = first_err {
            let fresh = crate::luau::timers::timer_ids_where(&host.lua, |_, path| path == rel)
                .into_iter()
                .filter(|id| !old_timers.contains(id))
                .collect::<Vec<_>>();
            crate::luau::timers::drop_timers(&host.lua, &fresh);
            if let Some(previous) = previous {
                host.registry.insert(rel.to_string(), previous);
            }
            return Err(format!("top-level de '{rel}': {err}"));
        }
    }
    crate::luau::timers::drop_timers(&host.lua, &old_timers);
    Ok(count)
}

/// Drena o watcher e aplica as recargas prontas. Corre ANTES do
/// `luau_on_add`/`luau_update` para o frame seguinte já usar o chunk novo.
#[allow(clippy::type_complexity)]
pub fn hot_reload_poll(
    mut host: ResMut<LuaScriptHost>,
    state: Option<ResMut<HotReloadState>>,
    scripts: Query<(Entity, &LuaScriptRef, Option<&Transform>)>,
) {
    let Some(mut state) = state else { return };
    for abs in state.ready_paths() {
        // O watcher entrega caminhos absolutos com o prefixo da raiz
        // canónica; o registry indexa caminhos relativos (com `/` — o
        // mesmo formato dos paths no XML).
        let Some(rel) = abs
            .strip_prefix(&state.root)
            .ok()
            .map(|rel| rel.to_string_lossy().replace('\\', "/"))
        else {
            continue;
        };
        // Só chunks que a engine já carregou (ficheiros novos não referem
        // nenhuma entidade — carregá-los seria estado morto no registry). Um
        // módulo `viber.load` editado sai do cache e TODOS os scripts
        // re-correm o top-level: guardam o `return` antigo em locals.
        let targets: Vec<String> = if host.registry.contains(&rel) {
            vec![rel.clone()]
        } else if crate::luau::game::evict_module(&host.lua, &rel) {
            host.registry.paths().into_iter().map(str::to_string).collect()
        } else {
            continue;
        };
        for target in targets {
            let entities: Vec<(Entity, Vec3)> = scripts
                .iter()
                .filter(|(_, lref, _)| lref.path == target)
                .map(|(entity, _, transform)| {
                    (
                        entity,
                        transform.map(|t| t.translation).unwrap_or(Vec3::ZERO),
                    )
                })
                .collect();
            match reload_script(&mut host, &state.root, &target, &entities) {
                Ok(count) => {
                    info!("hot-reload: '{target}' recarregado (top-level em {count} entidade(s))");
                }
                Err(e) => {
                    // Chunk antigo continua ativo — warn (não panic), a engine segue.
                    warn!("hot-reload falhou ({e}) — chunk antigo mantém-se");
                }
            }
        }
    }
}

/// `VIBER_HOT_RELOAD=0` desliga; ausente ou outro valor = ON.
pub fn enabled_from_env() -> bool {
    std::env::var("VIBER_HOT_RELOAD")
        .map(|v| v != "0")
        .unwrap_or(true)
}

#[cfg(test)]
mod tests {

    /// O watcher só pode reagir a ESCRITAS: a própria recarga lê o ficheiro,
    /// e um evento de ACESSO fechava o ciclo (recarga → leitura → evento →
    /// recarga, 3×/s para sempre no `simple-rpg`).
    #[test]
    fn access_events_never_trigger_a_reload() {
        use notify::EventKind;
        use notify::event::{AccessKind, CreateKind, ModifyKind, RemoveKind};
        assert!(!is_write_event(&EventKind::Access(AccessKind::Read)));
        assert!(!is_write_event(&EventKind::Access(AccessKind::Open(
            notify::event::AccessMode::Read
        ))));
        assert!(is_write_event(&EventKind::Modify(ModifyKind::Any)));
        assert!(is_write_event(&EventKind::Create(CreateKind::File)));
        assert!(is_write_event(&EventKind::Remove(RemoveKind::File)));
        // Backends que não classificam continuam a recarregar.
        assert!(is_write_event(&EventKind::Any));
    }
    use super::*;
    use std::fs;

    fn temp_scripts_dir(tag: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("viber-hot-reload-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("criar pasta temporária de scripts");
        dir
    }

    /// Reload feliz: chunk novo substitui o antigo, top-level re-corre e o
    /// novo global fica visível no env do script.
    #[test]
    fn reload_reruns_toplevel_with_new_code() {
        let dir = temp_scripts_dir("ok");
        let path = dir.join("npc.lua");
        fs::write(&path, "version = 1\nfunction on_update(dt) end").unwrap();

        let mut host = LuaScriptHost::new(dir.clone()).unwrap();
        host.ensure_loaded("npc.lua").unwrap();
        let hero = Entity::from_bits(0x00C0_FFE0_0000_0001);
        host.activate_at(hero, "npc.lua", Vec3::ONE).unwrap();

        fs::write(&path, "version = 2\nfunction on_update(dt) end").unwrap();
        let count = reload_script(&mut host, &dir, "npc.lua", &[(hero, Vec3::ONE)]).unwrap();
        assert_eq!(count, 1);

        // O global `version` do chunk NOVO substituiu o antigo.
        let version = host.script_global("npc.lua", "version").unwrap();
        assert_eq!(version, mlua::Value::Integer(2));
        let _ = fs::remove_dir_all(&dir);
    }

    /// Erro de compilação → Err, e o chunk ANTIGO continua funcional
    /// (globals do primeiro load intactos, `on_update` chamável).
    #[test]
    fn broken_file_keeps_old_chunk() {
        let dir = temp_scripts_dir("broken");
        let path = dir.join("npc.lua");
        fs::write(&path, "loaded = true\nfunction on_update(dt) end").unwrap();

        let mut host = LuaScriptHost::new(dir.clone()).unwrap();
        host.ensure_loaded("npc.lua").unwrap();
        let hero = Entity::from_bits(42);
        host.activate_at(hero, "npc.lua", Vec3::ZERO).unwrap();

        fs::write(&path, "this is not luau )))").unwrap();
        let err = reload_script(&mut host, &dir, "npc.lua", &[(hero, Vec3::ZERO)]);
        assert!(err.is_err(), "compilação quebrada tem de falhar");

        // Chunk antigo intacto: globals do primeiro load preservados.
        let loaded = host.script_global("npc.lua", "loaded").unwrap();
        assert_eq!(loaded, mlua::Value::Boolean(true));
        let _ = fs::remove_dir_all(&dir);
    }

    /// Top-level que rebenta a CORRER → Err, e o chunk antigo (com o seu
    /// `on_update`) é reposto; os timers do chunk antigo sobrevivem.
    #[test]
    fn runtime_error_in_toplevel_restores_old_chunk() {
        let dir = temp_scripts_dir("toplevel");
        let path = dir.join("npc.lua");
        fs::write(&path, "version = 1\nfunction on_update(dt) end").unwrap();

        let mut host = LuaScriptHost::new(dir.clone()).unwrap();
        host.ensure_loaded("npc.lua").unwrap();
        let hero = Entity::from_bits(42);
        host.activate_at(hero, "npc.lua", Vec3::ZERO).unwrap();

        fs::write(&path, "version = 2\nerror('boom')").unwrap();
        let err = reload_script(&mut host, &dir, "npc.lua", &[(hero, Vec3::ZERO)]);
        assert!(err.unwrap_err().contains("boom"));
        assert_eq!(
            host.script_global("npc.lua", "version").unwrap(),
            mlua::Value::Integer(1),
            "env antigo reposto"
        );
        assert!(host.registry.get("npc.lua").unwrap().on_update.is_some());
        let _ = fs::remove_dir_all(&dir);
    }

    /// Um reload bem-sucedido não soma timers: os do chunk antigo saem e o
    /// top-level novo re-regista os seus.
    #[test]
    fn reload_replaces_timers_instead_of_stacking() {
        let dir = temp_scripts_dir("timers");
        let path = dir.join("npc.lua");
        fs::write(&path, "viber.every(1, function() end)\nfunction on_update(dt) end").unwrap();

        let mut host = LuaScriptHost::new(dir.clone()).unwrap();
        host.ensure_loaded("npc.lua").unwrap();
        let hero = Entity::from_bits(42);
        host.activate_at(hero, "npc.lua", Vec3::ZERO).unwrap();
        let count = |host: &LuaScriptHost| {
            crate::luau::timers::timer_ids_where(&host.lua, |_, p| p == "npc.lua").len()
        };
        assert_eq!(count(&host), 1);
        reload_script(&mut host, &dir, "npc.lua", &[(hero, Vec3::ZERO)]).unwrap();
        reload_script(&mut host, &dir, "npc.lua", &[(hero, Vec3::ZERO)]).unwrap();
        assert_eq!(count(&host), 1);
        let _ = fs::remove_dir_all(&dir);
    }

    /// Ficheiro removido → Err de leitura, chunk antigo mantém-se.
    #[test]
    fn removed_file_fails_without_touching_registry() {
        let dir = temp_scripts_dir("removed");
        fs::write(dir.join("gone.lua"), "kept = 7").unwrap();

        let mut host = LuaScriptHost::new(dir.clone()).unwrap();
        host.ensure_loaded("gone.lua").unwrap();
        // O top-level só corre na ativação (ensure_loaded só compila).
        host.activate_at(Entity::from_bits(7), "gone.lua", Vec3::ZERO)
            .unwrap();

        fs::remove_file(dir.join("gone.lua")).unwrap();
        assert!(reload_script(&mut host, &dir, "gone.lua", &[]).is_err());
        assert!(host.registry.contains("gone.lua"), "chunk antigo fica");
        assert_eq!(
            host.script_global("gone.lua", "kept").unwrap(),
            mlua::Value::Integer(7)
        );
        let _ = fs::remove_dir_all(&dir);
    }

    /// O gate de ambiente (lógica pura — os testes correm em paralelo no
    /// mesmo processo, por isso o env real não é mutado).
    #[test]
    fn env_gate_logic() {
        let gate = |v: Option<String>| v.map(|v| v != "0").unwrap_or(true);
        assert!(gate(None));
        assert!(gate(Some("1".into())));
        assert!(gate(Some("on".into())));
        assert!(!gate(Some("0".into())));
    }
}
