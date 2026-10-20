//! O loop de frame dos scripts: `luau_on_add` (activa chunks), `luau_update`
//! (semeia snapshots, corre `on_update`, drena e aplica comandos,
//! fan-out de eventos), `aggro_alert_system` (hook `on_player_attack`) e
//! `luau_on_remove`. É o ÚNICO sítio onde o mundo Bevy é mutado por scripts.

use std::collections::{HashMap, HashSet};

use bevy::prelude::*;

use crate::player::Player;
use crate::vitals::{Health, Xp};

use super::commands::ScriptCommand;
use super::components::{
    DEFAULT_ACTIVATION_RADIUS, LuaScriptRef, ScriptActivation, ScriptInteraction, ScriptToast,
};
use super::ctx::{ScriptCtx, SurfaceCache, SurfaceRegistries};
use super::events::{ScriptEventQueue, ScriptGameEvent, fan_out_events};
use super::host::LuaScriptHost;
use super::input::key_code_from_str;
use super::sfx::match_gesture_clip;

/// Entidade nomeada com pose (snapshot de `viber.find`/`entity_position`).
type NamedPose = (Entity, &'static Name, &'static GlobalTransform);
/// Filtro do delta do snapshot: nome ou pose mudaram (herói excluído).
type NameOrPoseChanged = (
    Without<Player>,
    Or<(Changed<Name>, Changed<GlobalTransform>)>,
);

/// Tira `bits` do snapshot de nomes (`find`) e de posições.
fn forget_named(
    named: &mut HashMap<String, Vec<i64>>,
    positions: &mut HashMap<i64, (String, Vec3)>,
    bits: i64,
) {
    let Some((name, _)) = positions.remove(&bits) else {
        return;
    };
    if let Some(list) = named.get_mut(&name) {
        list.retain(|b| *b != bits);
        if list.is_empty() {
            named.remove(&name);
        }
    }
}

/// Hook `on_add`: when a [`LuaScriptRef`] appears, ensure its chunk is loaded
/// (from `<scripts_dir>/<path>`) and run its top level. Errors warn once and
/// never abort. A posição de spawn (`Transform` autoral — o `GlobalTransform`
/// ainda não propagou no primeiro frame) alimenta o `home` do wander.
pub fn luau_on_add(
    mut host: ResMut<LuaScriptHost>,
    added: Query<(Entity, &LuaScriptRef, Option<&Transform>), Added<LuaScriptRef>>,
) {
    for (entity, lref, transform) in &added {
        let origin = transform.map(|t| t.translation).unwrap_or(Vec3::ZERO);
        let result = host
            .ensure_loaded(&lref.path)
            .and_then(|()| host.activate_at(entity, &lref.path, origin));
        if let Err(e) = result {
            host.warn_once(&lref.path, &e);
        }
    }
}

/// Runs `on_update(dt)` of every live script, then applies queued commands
/// (posição com snap no terreno, teleporte, vitals do player, toasts,
/// interações). A script error is pcall'd: warned once, engine keeps running.
/// Estado por-runtime que sobrevive entre frames, agrupado num `SystemParam`:
/// o `luau_update` chegou ao limite de 16 parâmetros do Bevy.
#[derive(bevy::ecs::system::SystemParam)]
pub struct LuauRuntimeLocals<'w, 's> {
    /// Cena carregada e índice de cenas (`viber.scene_name`, `viber.change_scene`).
    pub scene: Option<bevy::ecs::system::Res<'w, crate::scene_loader::CurrentScene>>,
    pub scene_index: Option<bevy::ecs::system::Res<'w, crate::scene_loader::SceneIndex>>,
    /// Warn 1× por entidade sem rig/clip de gesto.
    pub gesture_warned: bevy::ecs::system::Local<'s, std::collections::HashSet<Entity>>,
    /// Warn 1× por kind de status desconhecido (`viber.apply_status`) — o
    /// script corre por frame; sem isto o warn unknown-kind virava spam.
    pub status_warned: bevy::ecs::system::Local<'s, std::collections::HashSet<String>>,
    /// Snapshots de quest/vault só com `Changed` (1.ª passagem força o seed).
    pub snapshots_seeded: bevy::ecs::system::Local<'s, bool>,
    /// Snapshot partilhado de estradas/água (`viber.on_road`/`in_water`).
    pub surfaces: bevy::ecs::system::Local<'s, SurfaceCache>,
    /// Locomoção das entidades scriptadas. Fica aqui, e não como parâmetro
    /// solto, porque o `luau_update` já está no teto de 16 parâmetros do Bevy.
    /// Disjunta da query `scripts` por componente (`AiLocomotion` /
    /// `LocomotionProfile` vs `Transform`), logo as duas coexistem.
    pub locomotion: Query<
        'w,
        's,
        (
            &'static mut crate::ai::AiLocomotion,
            Option<&'static mut crate::animation::LocomotionProfile>,
        ),
    >,
    /// Rato para `viber.input.*` (`"mouse1"`). `Option`: apps mínimas de teste
    /// não registam `ButtonInput<MouseButton>`.
    pub mouse: Option<
        bevy::ecs::system::Res<'w, bevy::input::ButtonInput<bevy::input::mouse::MouseButton>>,
    >,
    /// Fila de eventos engine→Lua (producers → fan-out por-path). `Option`
    /// pelas apps mínimas, como o resto.
    pub events: Option<bevy::ecs::system::ResMut<'w, ScriptEventQueue>>,
    /// Warn 1× por path com a fila de eventos cheia.
    pub events_dropped: bevy::ecs::system::Local<'s, std::collections::HashSet<String>>,
    /// HP mutável de entidades NÃO-player (`viber.entity_*`). A MESMA query
    /// alimenta o snapshot de leitura do frame (`entity_hp`). `Without<Player>`
    /// disjunta da query `players` (o herói vive em `player_hp`).
    pub healths: Query<
        'w,
        's,
        (
            Entity,
            &'static mut crate::vitals::Health,
            &'static GlobalTransform,
        ),
        Without<Player>,
    >,
    /// Props com `destructible` (`viber.destructibles`), filtrados à volta do herói.
    pub destructibles: Query<
        'w,
        's,
        (
            Entity,
            &'static crate::harvest::Destructible,
            &'static GlobalTransform,
        ),
        Without<Player>,
    >,
    /// Nomes + posição para os snapshots `viber.find`/`entity_position`/
    /// `nearby` (herói excluído).
    pub names: Query<'w, 's, (Entity, &'static Name, &'static GlobalTransform), Without<Player>>,
    /// `Name` de QUALQUER entidade — semeia o `ctx.self_name` de cada script
    /// ao frame, FORA do snapshot de nomes (o snapshot é completo hoje, mas o
    /// nome próprio não pode depender de nunca truncar: repro histórica dos
    /// portais do simple-rpg, 20 portais em ~79k nomes com cap 4096).
    pub entity_names: Query<'w, 's, &'static Name>,
    /// Grupos (`groups=`) — snapshot `ctx.groups` por frame.
    pub groups: Query<'w, 's, (Entity, &'static crate::luau::Groups)>,
    /// Conexões de sinal dos emissores (`connect-*`).
    pub connections: Query<'w, 's, &'static crate::luau::SignalConnections>,
    /// Cinemática a tocar (`viber.cinematic_playing`).
    pub cinematic: Option<bevy::ecs::system::Res<'w, crate::cinematic::CinematicPlayback>>,
    /// Menus que roubam input (`viber.menu_open`). `Option`: apps mínimas.
    pub menus: Option<bevy::ecs::system::Res<'w, crate::menus::MenusOpen>>,
    /// Snapshot de nomes semeado (a partir daí só deltas).
    pub names_seeded: bevy::ecs::system::Local<'s, bool>,
    /// Entidades nomeadas cujo nome ou pose mudou desde o último frame.
    pub names_changed: Query<'w, 's, NamedPose, NameOrPoseChanged>,
    /// Nomes removidos (inclui despawns) — saem do snapshot.
    pub names_removed: bevy::ecs::lifecycle::RemovedComponents<'w, 's, Name>,
    /// Props `prop-*` das entidades com script — semeia `ctx.self_props`.
    pub script_props: Query<'w, 's, &'static crate::luau::ScriptProps>,
    /// Warn 1× por entidade de dano/cura em quem não tem `Health`.
    pub entity_vitals_warned: bevy::ecs::system::Local<'s, HashSet<Entity>>,
    /// Warn 1× por chave para avisos que um script repete por frame (plugin
    /// ausente, recurso desconhecido, vault curto) — sem isto o log enchia.
    pub once_warned: bevy::ecs::system::Local<'s, HashSet<String>>,
    /// Health criado NESTE frame via `Commands` (invisível à query até ao fim
    /// do sistema) — os comandos seguintes do MESMO frame (set_max_hp seguido
    /// de damage, o padrão natural) leem o valor-sombra daqui.
    pub fresh_health: bevy::ecs::system::Local<'s, HashMap<Entity, (f32, f32)>>,
    /// Biblioteca de `<Prototype>` (snapshot `ctx.prototype_names`).
    pub prototypes: Option<bevy::ecs::system::Res<'w, crate::recipes::spawn::PrototypeLibrary>>,
    /// Fila de spawns de prototypes (`viber.spawn_prototype`).
    pub spawns: Option<bevy::ecs::system::ResMut<'w, crate::recipes::spawn::PendingScriptSpawns>>,
    /// Ações da UI reclamadas por scripts (`viber.own_action`).
    pub ui_action_owners: Option<bevy::ecs::system::ResMut<'w, crate::ui::actions::UiActionOwners>>,
    /// Pedido de save/load de script (`viber.save()` / `viber.load()`).
    pub save_request: Option<bevy::ecs::system::ResMut<'w, crate::save::SaveRequest>>,
    /// Sistemas nativos reclamados por scripts (`viber.own_system`).
    pub system_owners: Option<bevy::ecs::system::ResMut<'w, super::ownership::ScriptSystemOwners>>,
    /// Balão de diálogo do HUD — mesma query do diálogo nativo (`viber.say`).
    pub balloons: Query<
        'w,
        's,
        (
            &'static mut Visibility,
            &'static mut crate::hud::HudBalloon,
            &'static Children,
        ),
    >,
    pub balloon_texts: Query<'w, 's, &'static mut Text>,
    /// Warn 1× quando um mundo não declara `<DialogueBalloon>`.
    pub balloon_warned: bevy::ecs::system::Local<'s, bool>,
    /// Snapshot das quest defs (`viber.quest_def`) — semeado 1×.
    pub quest_defs_seeded: bevy::ecs::system::Local<'s, bool>,
    /// Recursos de FX das primitivas de script (`viber.burst/ring/shake/…`).
    pub fx: super::fx::ScriptFx<'w>,
    /// Fila das edições vivas do terreno (`viber.terrain.*`): o script
    /// enfileira, o `apply_terrain_edits` (PreUpdate seguinte) aplica ao
    /// overlay. `Option` pelas apps mínimas, como o resto.
    pub terrain_edits:
        Option<bevy::ecs::system::ResMut<'w, crate::terrain::delta::TerrainEditQueue>>,
    /// Pedidos de `viber.fire_projectile` (consumidos pelo `ProjectilePlugin`).
    pub projectiles: Option<bevy::ecs::system::ResMut<'w, crate::projectile::ProjectileQueue>>,
    /// Biomas revelados por atalaias (`viber.biome_revealed` / RevealBiome).
    /// `Option` pelas apps mínimas, como o resto.
    pub revealed: Option<bevy::ecs::system::Res<'w, crate::travel::RevealedBiomes>>,
    /// Pose local de entidades SEM script e SEM `Player` (`viber.set_pose`
    /// em rodas/karts rivais). Disjunta das queries `scripts` e `players`.
    pub poses: Query<'w, 's, &'static mut Transform, (Without<LuaScriptRef>, Without<Player>)>,
    /// Loops de SFX pedidos por script (`viber.sound_loop`).
    pub sound_loops: Option<bevy::ecs::system::ResMut<'w, crate::music::ScriptSoundLoops>>,
    /// Layer de BGM forçada (`viber.music.layer`).
    pub music_layer: Option<bevy::ecs::system::ResMut<'w, crate::music::MusicLayerOverride>>,
}

#[allow(clippy::too_many_arguments, clippy::type_complexity)]
pub fn luau_update(
    time: Res<Time>,
    keys: Res<ButtonInput<KeyCode>>,
    // `Option`: apps mínimas (testes, headless) montam sistemas soltos sem o
    // recurso — sem foco, o `interacted` cai no teste de alcance de sempre.
    focus: Option<Res<crate::interact::InteractionFocus>>,
    mut host: ResMut<LuaScriptHost>,
    mut scripts: Query<
        (
            Entity,
            &LuaScriptRef,
            Option<&mut Transform>,
            // O MUNDO da entidade. O `Transform` é LOCAL: um NPC dentro do
            // grupo `city` (assentado a y≈38,7 no terreno) tem local y=0, e
            // usar isso como posição punha-o 38 m abaixo do herói — o
            // `viber.interacted` (raio 3,5 m) nunca disparava e o [E] "não
            // fazia nada" em toda a cidade (repro do utilizador 2026-09-12).
            Option<&GlobalTransform>,
            Option<&ScriptActivation>,
            Option<&ScriptInteraction>,
        ),
        Without<Player>,
    >,
    mut players: Query<
        (
            Entity,
            &GlobalTransform,
            Option<&mut Transform>,
            Option<&mut Health>,
            Option<&mut Xp>,
        ),
        With<crate::player::Player>,
    >,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut toasts: bevy::ecs::message::MessageWriter<ScriptToast>,
    mut sfx: bevy::ecs::message::MessageWriter<crate::ambient::SfxEvent>,
    mut hurts: bevy::ecs::message::MessageWriter<crate::feedback::PlayerHurt>,
    mut quests: Option<ResMut<crate::quests::QuestLog>>,
    mut vault: Option<ResMut<crate::economy::Vault>>,
    // Gestos (`viber.gesture`): rig da entidade + os AnimationPlayers das
    // cenas glTF (play_action precisa dos dois).
    mut animators: Query<&mut crate::animation::CharacterAnimator>,
    mut animation_players: crate::animation::PlayerQuery,
    // Estado persistente agrupado num `SystemParam` (o Bevy limita sistemas a
    // 16 parâmetros — este trio chegou a esse teto).
    mut locals: LuauRuntimeLocals,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    let elapsed = time.elapsed_secs_f64();
    let (player_pos, mut player_components, player_forward) = match players.single_mut() {
        Ok((p_entity, global, transform, health, xp)) => {
            // Forward do modelo (+Z) na MESMA extração (o borrow mutável de
            // `players` vive em `player_components`).
            let f = crate::combat::hero_forward(global);
            (
                Some(global.translation()),
                Some((p_entity, transform, health, xp)),
                Vec2::new(f.x, f.z),
            )
        }
        Err(_) => (None, None, Vec2::ZERO),
    };
    let just_pressed: Vec<KeyCode> = keys.get_just_pressed().copied().collect();
    let keys_down: Vec<KeyCode> = keys.get_pressed().copied().collect();
    let keys_released: Vec<KeyCode> = keys.get_just_released().copied().collect();

    // Snapshots de quest/vault para `viber.quest_state`/`vault_get` — só
    // reconstruídos quando o recurso mudou desde o último run (ou no arranque):
    // 21 defs + o vault por frame era trabalho morto. `is_changed` num `ResMut`
    // apanha mutações de outros sistemas entre runs e as feitas por ESTE
    // sistema no run anterior — os scripts continuam a ler valores frescos no
    // frame seguinte à mudança. O vault suja também o snapshot de quests: o
    // status/progresso de misses de coleta depende do vault.
    let seeded = *locals.snapshots_seeded;
    *locals.snapshots_seeded = true;
    let vault_dirty = !seeded || vault.as_ref().is_some_and(|v| v.is_changed());
    let quests_dirty = vault_dirty || quests.as_ref().is_some_and(|q| q.is_changed());
    // Snapshot dos estados de quest para `viber.quest_state` (frame-start).
    if quests_dirty && let Some(quests) = quests.as_deref_mut() {
        let snapshot: std::collections::HashMap<String, String> = quests
            .defs
            .iter()
            .map(|d| {
                (
                    d.id.clone(),
                    crate::quests::status_name(quests.status(&d.id, vault.as_deref())).to_string(),
                )
            })
            .collect();
        if let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>() {
            ctx.quest_states = snapshot;
        }
    }
    // Snapshot do vault para `vault_get`/`item_count`.
    if vault_dirty && let Some(vault) = vault.as_deref() {
        let snapshot: std::collections::HashMap<String, u32> = [
            ("gold", vault.gold),
            ("wood", vault.wood),
            ("stone", vault.stone),
        ]
        .into_iter()
        .chain(vault.items.iter().map(|(k, v)| (k.as_str(), *v)))
        .map(|(k, v)| (k.to_string(), v))
        .collect();
        if let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>() {
            ctx.vault = snapshot;
        }
    }
    // Snapshot dos biomas revelados por atalaias (`viber.biome_revealed`).
    let revealed_dirty = locals
        .revealed
        .as_ref()
        .is_some_and(|r| !seeded || r.is_changed());
    if revealed_dirty
        && let Some(revealed) = locals.revealed.as_deref()
        && let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>()
    {
        ctx.revealed = revealed.0.clone();
    }

    // Semeia as teclas ANTES de correr os on_update: `viber.interacted`
    // tem de ver as teclas pressionadas NESTE frame (semear só no fim
    // fazia os scripts lerem o snapshot do frame anterior).
    if let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>() {
        ctx.just_pressed = just_pressed.clone();
        ctx.keys_down = keys_down;
        ctx.keys_released = keys_released;
        match locals.mouse.as_deref() {
            Some(mouse) => {
                ctx.mouse_pressed = mouse.get_just_pressed().copied().collect();
                ctx.mouse_down = mouse.get_pressed().copied().collect();
                ctx.mouse_released = mouse.get_just_released().copied().collect();
            }
            None => {
                ctx.mouse_pressed.clear();
                ctx.mouse_down.clear();
                ctx.mouse_released.clear();
            }
        }
        // Vencedor por tecla deste frame (decidido no PreUpdate).
        ctx.interaction_focus = focus
            .as_deref()
            .map(crate::interact::InteractionFocus::snapshot)
            .unwrap_or_default();
        // Snapshot do HP do herói para `viber.player_hp` (frame-start).
        ctx.player_hp = player_components
            .as_ref()
            .and_then(|(_, _, health, _)| health.as_deref().map(|h| (h.current, h.max)));
        ctx.player_bits = player_components
            .as_ref()
            .map(|(entity, _, _, _)| entity.to_bits() as i64);
        // Forward do herói (modelo olha +Z) para `viber.player_forward`.
        ctx.player_forward = player_forward;
        ctx.cinematic = locals
            .cinematic
            .as_deref()
            .and_then(|c| c.playing().map(str::to_owned));
        ctx.menu_open = locals.menus.as_deref().is_some_and(|menus| menus.any());
        // Handle de leitura do terreno para `viber.ground_below` — dois
        // clones de Arc por frame; o terreno é imutável pós-bootstrap.
        ctx.terrain = terrain.as_deref().map(|rt| rt.reader());
        // Imutável DENTRO de uma cena; uma troca de cena insere um terreno novo (ou nenhum).
        match terrain.as_ref() {
            Some(rt) if locals.surfaces.is_none() || rt.is_added() => {
                *locals.surfaces = Some(std::sync::Arc::new(SurfaceRegistries {
                    roads: rt.roads.clone(),
                    water: rt.water.clone(),
                }));
            }
            Some(_) => {}
            None => *locals.surfaces = None,
        }
        ctx.scene = locals.scene.as_deref().and_then(|s| s.id.clone());
        ctx.scene_entry = locals.scene.as_deref().and_then(|s| s.entry.clone());
        if let Some(index) = locals.scene_index.as_ref()
            && (index.is_changed() || ctx.scene_ids.is_empty())
        {
            ctx.scene_ids = std::sync::Arc::new(index.ids.clone());
        }
        ctx.surfaces = locals.surfaces.clone();
        // Snapshots de nomes e HP para `viber.find`/`entity_hp` — início de
        // frame: entidades novas são vistas no frame seguinte (como tudo).
        // SEM cap: o `.take(4096)` antigo cortava por ordem arbitrária de
        // query e em mundos grandes (`simple-rpg`: ~79k entidades nomeadas)
        // `find`/`find_all`/`entity_position(id)` perdiam entidades — 10 dos
        // 20 portais do simple-rpg eram invisíveis aos scripts. Os vetores
        // por nome ficam ORDENADOS por bits de entidade: `find_all` devolve
        // ordem estável entre frames/máquinas (scripts que cacheiam registos
        // no arranque não podem ver ordens diferentes).
        {
            // INCREMENTAL: reconstruir 79k nomes por frame custava ~18 ms
            // (simple-rpg). Semeia 1× e depois só aplica o que mudou
            // (Name/GlobalTransform alterados, Names removidos/despawns) —
            // a vegetação e os props estáticos não custam nada por frame.
            if !*locals.names_seeded {
                *locals.names_seeded = true;
                locals.names_removed.clear();
                let mut named: HashMap<String, Vec<i64>> = HashMap::new();
                let mut positions: HashMap<i64, (String, Vec3)> = HashMap::new();
                for (e, name, transform) in locals.names.iter() {
                    let bits = e.to_bits() as i64;
                    named.entry(name.to_string()).or_default().push(bits);
                    positions.insert(bits, (name.to_string(), transform.translation()));
                }
                for bits in named.values_mut() {
                    bits.sort_unstable();
                }
                ctx.named_entities = named;
                ctx.pos_snapshot = positions;
            } else {
                let ctx = &mut *ctx;
                for e in locals.names_removed.read() {
                    forget_named(
                        &mut ctx.named_entities,
                        &mut ctx.pos_snapshot,
                        e.to_bits() as i64,
                    );
                }
                for (e, name, transform) in locals.names_changed.iter() {
                    let bits = e.to_bits() as i64;
                    let name = name.as_str();
                    let renamed = ctx
                        .pos_snapshot
                        .get(&bits)
                        .is_some_and(|(old, _)| old != name);
                    if renamed {
                        forget_named(&mut ctx.named_entities, &mut ctx.pos_snapshot, bits);
                    }
                    let list = ctx.named_entities.entry(name.to_owned()).or_default();
                    if let Err(at) = list.binary_search(&bits) {
                        list.insert(at, bits);
                    }
                    ctx.pos_snapshot
                        .insert(bits, (name.to_owned(), transform.translation()));
                }
            }
            let mut groups: HashMap<String, Vec<i64>> = HashMap::new();
            for (e, member_of) in locals.groups.iter() {
                for group in member_of.0.iter() {
                    groups
                        .entry(group.clone())
                        .or_default()
                        .push(e.to_bits() as i64);
                }
            }
            for bits in groups.values_mut() {
                bits.sort_unstable();
            }
            ctx.groups = groups;
            let mut hp: HashMap<i64, (f32, f32)> = HashMap::new();
            for (e, h, _) in locals.healths.iter() {
                hp.insert(e.to_bits() as i64, (h.current, h.max));
            }
            ctx.hp_snapshot = hp;
            ctx.destructibles.clear();
            if let Some(hero) = player_pos {
                for (e, prop, global) in locals.destructibles.iter() {
                    let pos = global.translation();
                    if pos.distance(hero) > 40.0 {
                        continue;
                    }
                    let (kind, amount) = prop
                        .resource
                        .clone()
                        .unwrap_or_else(|| (String::new(), 0));
                    ctx.destructibles.push(super::ctx::DestructibleSnap {
                        id: e.to_bits() as i64,
                        hits_left: prop.hits.saturating_sub(prop.hits_taken),
                        kind,
                        amount,
                        pos,
                        range: prop.range,
                        style: match prop.break_style {
                            crate::harvest::BreakStyle::Fall => "fall",
                            crate::harvest::BreakStyle::Shatter => "shatter",
                            crate::harvest::BreakStyle::Burst => "burst",
                        }
                        .to_string(),
                        popup: prop.popup.clone().unwrap_or_default(),
                    });
                }
            }
            ctx.hostiles.clear();
            for (e, script, _, global, _, _) in scripts.iter() {
                if !crate::combat::is_hostile_script(&script.path) {
                    continue;
                }
                let Some(global) = global else { continue };
                let Some((cur, max)) = ctx.hp_snapshot.get(&(e.to_bits() as i64)) else { continue };
                if cur <= 0.0 {
                    continue;
                }
                ctx.hostiles.push(super::ctx::HostileSnap {
                    id: e.to_bits() as i64,
                    kind: crate::combat::script_kind(&script.path),
                    pos: global.translation(),
                    hp: cur,
                    max,
                });
            }
            if let Some(lib) = locals.prototypes.as_deref() {
                ctx.prototype_names = lib.0.keys().cloned().collect();
            }
            // Definições de quest (estáticas após o load) — semeia 1×.
            if !*locals.quest_defs_seeded
                && let Some(quests) = quests.as_deref()
            {
                *locals.quest_defs_seeded = true;
                ctx.quest_defs = quests
                    .defs
                    .iter()
                    .map(|d| super::ctx::QuestDefLite {
                        id: d.id.clone(),
                        title: d.title.clone(),
                        npc: d.npc.clone(),
                        biome: d.biome.clone(),
                        kind: d.objective.kind.clone(),
                        target: d.objective.target.clone(),
                        count: d.objective.count,
                        radius: d.objective.radius.unwrap_or(0.0),
                        gold: d.rewards.gold,
                        xp: d.rewards.xp,
                        items: d.rewards.items.clone(),
                        lines_intro: d.lines_intro.clone(),
                        lines_progress: d.lines_progress.clone(),
                        lines_complete: d.lines_complete.clone(),
                    })
                    .collect();
            }
        }
    }

    // Fan-out de eventos engine→Lua: cada evento vira tabela Lua e entra na
    // fila de CADA script subscrito (quem chamou `viber.events()` pelo menos
    // uma vez — a presença da chave no registry É a subscrição). Corre ANTES
    // do loop de on_update: o script vê o evento no frame em que corre.
    let incoming = locals
        .events
        .as_mut()
        .map(|q| std::mem::take(&mut q.0))
        .unwrap_or_default();
    // Fan-out de eventos engine→Lua (pcall-style — erro de Lua num evento
    // não derruba o frame) e tick dos timers (`viber.after`/`every`), ambos
    // ANTES dos `on_update`: o script vê o evento/timer no próprio frame.
    fan_out_events(&host.lua, &incoming, &mut locals.events_dropped);
    super::timers::tick(
        &mut host,
        super::timers::TickFrame {
            elapsed,
            dt,
            player: player_pos,
            origin_of: |owner| {
                scripts
                    .get(owner)
                    .ok()
                    .and_then(|(_, _, transform, global, _, _)| {
                        global
                            .map(GlobalTransform::translation)
                            .or_else(|| transform.map(|t| t.translation))
                    })
            },
        },
    );

    for (entity, lref, transform, global, activation, interaction) in &mut scripts {
        // Posição no MUNDO (o `Transform` sozinho é local ao grupo pai).
        let Some(origin) = global
            .map(GlobalTransform::translation)
            .or_else(|| transform.as_ref().map(|t| t.translation))
        else {
            continue;
        };
        // Congelamento (LOD de IA): além do raio de ativação o on_update nem
        // roda — inimigo distante custa zero lógica (e a animação para junto).
        let radius = activation
            .map(|a| a.radius)
            .unwrap_or(DEFAULT_ACTIVATION_RADIUS);
        if let Some(p) = player_pos
            && origin.distance(p) > radius
        {
            continue;
        }
        // Range de interação da entidade actual (para `viber.interacted`).
        if let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>() {
            ctx.interaction_range = interaction.map(|i| i.range);
            // Nome PRÓPRIO da entidade que vai correr (fora do snapshot com
            // cap — ver `entity_names`).
            ctx.self_name = locals.entity_names.get(entity).ok().map(Name::to_string);
            ctx.self_props = locals.script_props.get(entity).ok().map(|p| p.0.clone());
        }
        // Secção "scripts" do profiler: um escopo por ficheiro (igual ao
        // `script/<file>` antigo). record_script auto-gateia em freeze.
        let script_t0 = std::time::Instant::now();
        if let Err(e) = host.run_update(entity, &lref.path, dt, origin, player_pos, elapsed) {
            host.warn_once(&lref.path, &e);
        }
        crate::profiler::timed::record_script(
            &lref.path,
            script_t0.elapsed().as_secs_f32() * 1000.0,
        );
    }

    // Sinais (`viber.emit`) e `viber.call_group`: entregues DEPOIS dos
    // on_update, no mesmo frame, com o contexto do alvo. Handlers que emitem
    // abrem nova vaga; o que sobrar das vagas fica para o frame seguinte.
    // Alvos fora do raio de ativação recebem na mesma (um sinal é um evento —
    // a porta longe tem de abrir).
    for _ in 0..super::signals::MAX_SIGNAL_ROUNDS {
        let calls = super::signals::drain(&host.lua);
        if calls.is_empty() {
            break;
        }
        let mut deliveries: Vec<(Entity, String, mlua::Table, Option<i64>)> = Vec::new();
        if let Some(ctx) = host.lua.app_data_ref::<ScriptCtx>() {
            let members = |bits: Option<&Vec<i64>>| bits.cloned().unwrap_or_default();
            for call in calls {
                match &call.kind {
                    super::signals::CallKind::Emit { signal } => {
                        let Some(emitter) = call.from.and_then(|b| Entity::try_from_bits(b as u64))
                        else {
                            continue;
                        };
                        let Ok(connections) = locals.connections.get(emitter) else {
                            continue; // sinal sem ouvintes: como no Godot, nada acontece
                        };
                        for conn in connections.of(signal) {
                            use super::exports::ConnTarget;
                            let targets = match &conn.target {
                                ConnTarget::Name(name) if name == "self" => {
                                    vec![emitter.to_bits() as i64]
                                }
                                ConnTarget::Name(name) => members(ctx.named_entities.get(name)),
                                ConnTarget::Group(group) => members(ctx.groups.get(group)),
                            };
                            if targets.is_empty()
                                && locals
                                    .once_warned
                                    .insert(format!("signal-target:{signal}:{conn}"))
                            {
                                warn!(
                                    "[luau] sinal '{signal}': alvo '{}' sem entidades",
                                    conn.target
                                );
                            }
                            for bits in targets {
                                if let Some(target) = Entity::try_from_bits(bits as u64) {
                                    deliveries.push((
                                        target,
                                        conn.method.clone(),
                                        call.args.clone(),
                                        call.from,
                                    ));
                                }
                            }
                        }
                    }
                    super::signals::CallKind::Group { group, method } => {
                        for bits in members(ctx.groups.get(group)) {
                            if let Some(target) = Entity::try_from_bits(bits as u64) {
                                deliveries.push((
                                    target,
                                    method.clone(),
                                    call.args.clone(),
                                    call.from,
                                ));
                            }
                        }
                    }
                }
            }
        }
        for (target, method, args, from) in deliveries {
            let Ok((_, lref, transform, global, _, interaction)) = scripts.get(target) else {
                if locals
                    .once_warned
                    .insert(format!("signal-noscript:{target}:{method}"))
                {
                    warn!("[luau] {method}: o alvo {target} não tem script — ignorado");
                }
                continue;
            };
            let path = lref.path.clone();
            let origin = global
                .map(GlobalTransform::translation)
                .or_else(|| transform.as_ref().map(|t| t.translation))
                .unwrap_or_default();
            if let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>() {
                ctx.interaction_range = interaction.map(|i| i.range);
                ctx.self_name = locals.entity_names.get(target).ok().map(Name::to_string);
                ctx.self_props = locals.script_props.get(target).ok().map(|p| p.0.clone());
            }
            match host.run_method(
                target,
                &path,
                origin,
                &method,
                super::signals::unpack(&args),
                from,
            ) {
                Ok(true) => {}
                Ok(false) => {
                    if locals
                        .once_warned
                        .insert(format!("signal-method:{path}:{method}"))
                    {
                        warn!(
                            "[luau] {path}: não define `function {method}(…)` (alvo de sinal/grupo)"
                        );
                    }
                }
                Err(e) => {
                    host.warn_once(&path, &e);
                }
            }
        }
    }

    // Recolhe os comandos enfileirados por TODOS os scripts (as teclas já
    // foram semeadas antes do loop).
    let mut queued: Vec<ScriptCommand> = Vec::new();
    if let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>() {
        queued = std::mem::take(&mut ctx.commands);
    }
    // O mapa-sombra é do frame anterior — os Commands já aplicaram.
    locals.fresh_health.clear();
    for command in queued {
        match command {
            ScriptCommand::MoveTowards {
                entity,
                goal,
                speed,
            } => {
                // A posição actual vem do MUNDO (o alvo é mundo); sem ela não
                // há direcção nem chegada.
                let here = match scripts.get(entity) {
                    Ok((_, _, transform, global, _, _)) => global
                        .map(GlobalTransform::translation)
                        .or(transform.map(|t| t.translation)),
                    Err(_) => None,
                };
                let Some(here) = here else { continue };
                let here = Vec2::new(here.x, here.z);
                match locals.locomotion.get_mut(entity) {
                    Ok((mut loco, profile)) => {
                        loco.drive_to(here, goal, speed, dt);
                        if let Some(mut profile) = profile {
                            profile.observe(speed);
                        }
                    }
                    Err(_) => {
                        let mut loco = crate::ai::AiLocomotion::default();
                        loco.drive_to(here, goal, speed, dt);
                        let mut profile = crate::animation::LocomotionProfile::default();
                        profile.observe(speed);
                        commands.entity(entity).try_insert((loco, profile));
                    }
                }
            }
            ScriptCommand::MoveBy(entity, velocity) => {
                // A velocidade desejada vai para o [`crate::ai::AiLocomotion`];
                // o passo, a rampa de aceleração, o snap de Y e a viragem são
                // do consumidor único (`apply_ai_locomotion`, PostUpdate).
                // Entidades ainda sem o componente recebem-no aqui — o perfil
                // de locomoção aprende a velocidade comandada no mesmo gesto,
                // que é o que faz os 17 scripts do exemplo acertarem o clip e
                // a cadência sem uma linha de autoria.
                let speed = velocity.length();
                match locals.locomotion.get_mut(entity) {
                    Ok((mut loco, profile)) => {
                        loco.drive(velocity);
                        if let Some(mut profile) = profile {
                            profile.observe(speed);
                        }
                    }
                    Err(_) => {
                        let mut loco = crate::ai::AiLocomotion::default();
                        loco.drive(velocity);
                        let mut profile = crate::animation::LocomotionProfile::default();
                        profile.observe(speed);
                        commands.entity(entity).try_insert((loco, profile));
                    }
                }
            }
            ScriptCommand::FaceTowards(entity, target) => {
                // Direção no MUNDO (o alvo é o herói, em mundo).
                let here = match scripts.get(entity) {
                    Ok((_, _, transform, global, _, _)) => global
                        .map(GlobalTransform::translation)
                        .or(transform.map(|t| t.translation)),
                    Err(_) => None,
                };
                let Some(here) = here else { continue };
                let dir = Vec2::new(target.x - here.x, target.z - here.z);
                // Pedido de olhar, não ordem: `apply_ai_locomotion` só o honra
                // com a entidade praticamente parada. A andar, o corpo aponta
                // para onde VAI — é esta regra que mata o "andar de lado" dos
                // scripts que chamam `face_player()` e `move_towards()` no
                // mesmo frame.
                match locals.locomotion.get_mut(entity) {
                    Ok((mut loco, _)) => loco.look_at(dir),
                    Err(_) => {
                        let mut loco = crate::ai::AiLocomotion::default();
                        loco.look_at(dir);
                        commands.entity(entity).try_insert(loco);
                    }
                }
            }
            ScriptCommand::SetLocomotion {
                entity,
                walk,
                run,
                turn_rate,
            } => {
                let mut profile = crate::animation::LocomotionProfile::authored(walk, run);
                if let Some(turn_rate) = turn_rate {
                    profile.turn_rate = turn_rate;
                }
                match locals.locomotion.get_mut(entity) {
                    Ok((_, Some(mut existing))) => *existing = profile,
                    Ok((_, None)) => {
                        commands.entity(entity).try_insert(profile);
                    }
                    Err(_) => {
                        commands
                            .entity(entity)
                            .try_insert((crate::ai::AiLocomotion::default(), profile));
                    }
                }
            }
            ScriptCommand::ChangeScene { scene, entry, via } => {
                let request = crate::scene_loader::ChangeScene {
                    scene,
                    arrival: crate::scene_loader::Arrival::from_entry(entry.as_deref()),
                    via_portal: via,
                    toast: None,
                    sound: None,
                    came_from: None,
                    return_pose: None,
                };
                commands.queue(move |world: &mut World| {
                    if let Err(error) = crate::scene_loader::request_change(world, request) {
                        warn!(target: "viber::luau", "viber.change_scene: {error:#}");
                    }
                });
            }
            ScriptCommand::TeleportPlayer(pos) => {
                if let Some((entity, Some(transform), _, _)) = player_components.as_mut() {
                    // ASSENTA no destino em vez de largar o herói no ar: as
                    // colunas do destino podem estar por assar (vinha de uma
                    // bolsa de interior a 2,6 km) e, sem collider, o herói
                    // caía pelo mundo fora antes de o streaming o apanhar.
                    // A superfície analítica existe sempre — é a mesma que o
                    // `player_movement` usa como chão de último recurso.
                    let landed = crate::player::landing_position(terrain.as_deref(), pos);
                    transform.translation = landed;
                    commands
                        .entity(*entity)
                        .try_insert(crate::player::TeleportSettle::default());
                }
            }
            ScriptCommand::PushPlayer { velocity, iframes } => {
                if let Some((entity, _, _, _)) = player_components.as_ref() {
                    commands
                        .entity(*entity)
                        .try_insert(crate::physics_fx::Knockback { velocity });
                    if iframes > 0.0 {
                        commands
                            .entity(*entity)
                            .try_insert(crate::feedback::Invulnerable { timer: iframes });
                    }
                }
            }
            ScriptCommand::AddXp(gain) => {
                if let Some((_, _, _, Some(xp))) = player_components.as_mut() {
                    crate::vitals::gain_xp(xp, gain);
                }
            }
            ScriptCommand::DamagePlayer { amount, from } => {
                // Path único de dano: o feedback aplica (i-frames, vinheta,
                // número flutuante, morte, knockback) no próximo processamento.
                hurts.write(crate::feedback::PlayerHurt {
                    amount,
                    status: false,
                    from,
                });
                if std::env::var_os("VIBER_COMBAT_DEBUG").is_some() {
                    info!(target: "viber::combat", "damage {amount} pedido por script");
                }
            }
            ScriptCommand::FireProjectile {
                template,
                origin,
                target,
                shooter,
            } => {
                if let Some(queue) = locals.projectiles.as_deref_mut() {
                    queue.requests.push(crate::projectile::ProjectileRequest {
                        template,
                        origin,
                        target,
                        shooter,
                    });
                } else if locals.once_warned.insert("fire_projectile".into()) {
                    warn!("viber.fire_projectile sem ProjectilePlugin — pedido ignorado");
                }
            }
            ScriptCommand::HealPlayer(amount) => {
                if let Some((_, _, Some(health), _)) = player_components.as_mut() {
                    health.current = (health.current + amount).min(health.max);
                }
            }
            ScriptCommand::ApplyStatus { kind, secs } => {
                if kind.eq_ignore_ascii_case("venom") {
                    if let Some((p_entity, _, _, _)) = player_components.as_mut() {
                        commands
                            .entity(*p_entity)
                            .try_insert(crate::feedback::StatusEffects {
                                venom: secs.max(0.0),
                                venom_tick: 0.0,
                            });
                    }
                } else if locals.status_warned.insert(kind.clone()) {
                    warn!(target: "viber::luau", "apply_status: kind desconhecido '{kind}'");
                }
            }
            ScriptCommand::Toast(msg) => {
                info!(target: "viber::luau", "[toast] {msg}");
                toasts.write(ScriptToast(msg));
            }
            ScriptCommand::SetInteraction {
                entity,
                label,
                key,
                range,
            } => {
                if let Some(code) = key_code_from_str(&key) {
                    commands.entity(entity).try_insert(ScriptInteraction {
                        label,
                        key: code,
                        // ÚNICO sítio onde o alcance autorado vira alcance
                        // efetivo: daqui para a frente (prompt, foco,
                        // `interacted`, colheita) todos leem o mesmo número.
                        range: crate::interact::scaled_range(range),
                    });
                }
            }
            ScriptCommand::Despawn(entity) => {
                commands.entity(entity).try_despawn();
            }
            ScriptCommand::Gesture {
                entity,
                name,
                speed,
            } => {
                let Ok(mut animator) = animators.get_mut(entity) else {
                    if locals.gesture_warned.insert(entity) {
                        warn!(target: "viber::luau",
                            "viber.gesture('{name}'): entidade sem CharacterAnimator — \
                             cena glTF ainda a carregar ou rig sem clips");
                    }
                    continue;
                };
                let Some(index) = match_gesture_clip(&animator, &name) else {
                    if locals.gesture_warned.insert(entity) {
                        warn!(target: "viber::luau",
                            "viber.gesture('{name}'): nenhum clip do rig corresponde (clips: {:?})",
                            animator.clip_names);
                    }
                    continue;
                };
                let Some(node) = animator.nodes.get(index).copied() else {
                    continue;
                };
                // Blend de gesto ~250 ms como o npc_gesture_system; one-shot
                // devolve o rig ao driver no fim do clip. `speed` escala a
                // reprodução (`viber.play_clip`; 1.0 = nominal).
                if (speed - 1.0).abs() < 1e-3 {
                    crate::animation::play_action(
                        &mut animator,
                        &mut animation_players,
                        node,
                        std::time::Duration::from_millis(250),
                        false,
                    );
                } else {
                    crate::animation::play_action_scaled(
                        &mut animator,
                        &mut animation_players,
                        node,
                        std::time::Duration::from_millis(250),
                        false,
                        speed,
                    );
                }
            }
            ScriptCommand::TerrainEdit { edit } => {
                if let Some(queue) = locals.terrain_edits.as_deref_mut() {
                    if !queue.push(edit) && locals.once_warned.insert("terrain_edit_full".into()) {
                        warn!(
                            "viber.terrain.*: fila de edições cheia ({} pendentes) — pedidos \
                             descartados até a fila escoar",
                            crate::terrain::delta::EDIT_QUEUE_CAP
                        );
                    }
                } else {
                    // Apps mínimas sem o plugin de terreno: warn 1× (a fila
                    // nem existe — o pedido não pode ser aceite em silêncio).
                    if locals.once_warned.insert("terrain_edit".into()) {
                        warn!("viber.terrain.* sem TerrainEditQueue registada — pedido ignorado");
                    }
                }
            }
            ScriptCommand::PlaySfx { clip, position } => {
                sfx.write(crate::ambient::SfxEvent { clip, position });
            }
            ScriptCommand::QuestAccept(id) => {
                let Some(quests) = quests.as_deref_mut() else {
                    continue;
                };
                let title = quests.def(&id).map(|d| d.title.clone());
                if quests.accept(&id) {
                    toasts.write(ScriptToast(format!(
                        "Quest aceita: {}",
                        title.unwrap_or(id)
                    )));
                }
            }
            ScriptCommand::QuestTurnIn(id) => {
                let (Some(quests), Some(vault_ref)) = (quests.as_deref_mut(), vault.as_deref_mut())
                else {
                    continue;
                };
                let title = quests.def(&id).map(|d| d.title.clone());
                let Some(rewards) = quests.turn_in(&id, Some(vault_ref)) else {
                    continue;
                };
                if let Some(events) = locals.events.as_deref_mut() {
                    events.push(ScriptGameEvent::QuestDone { id: id.clone() });
                }
                {
                    if rewards.xp > 0
                        && let Some((_, _, _, Some(xp))) = player_components.as_mut()
                    {
                        crate::vitals::gain_xp(xp, rewards.xp);
                    }
                    if rewards.gold > 0 {
                        vault_ref.add_resource("gold", rewards.gold);
                    }
                    for item in &rewards.items {
                        if let Some((item_id, n)) = crate::quests::parse_item_reward(item) {
                            vault_ref.item_add(&item_id, n);
                        }
                    }
                    toasts.write(ScriptToast(format!(
                        "Quest entregue: {} (+{} XP{})",
                        title.unwrap_or_else(|| id.clone()),
                        rewards.xp,
                        if rewards.gold > 0 {
                            format!(", +{} ouro", rewards.gold)
                        } else {
                            String::new()
                        }
                    )));
                }
            }
            ScriptCommand::QuestReport { target, amount } => {
                let Some(quests) = quests.as_deref_mut() else {
                    continue;
                };
                for ready in quests.report_progress(&target, amount) {
                    if let Some(def) = quests.def(&ready) {
                        toasts.write(ScriptToast(format!(
                            "Objetivo completo: {} — volta ao NPC",
                            def.title
                        )));
                    }
                }
            }
            ScriptCommand::QuestVisit(place) => {
                let Some(quests) = quests.as_deref_mut() else {
                    continue;
                };
                for ready in quests.report_visit(&place) {
                    if let Some(def) = quests.def(&ready) {
                        toasts.write(ScriptToast(format!(
                            "Objetivo completo: {} — volta ao NPC",
                            def.title
                        )));
                    }
                }
            }
            ScriptCommand::VaultAdd {
                kind,
                amount,
                from_collect,
            } => {
                if let Some(vault) = vault.as_deref_mut() {
                    // report_collect serve recursos (gold/wood/stone) E
                    // itens de objetivo ("dark-wood", "bog-moss"): sem o
                    // fallback item_add, quests collect de ITEM eram
                    // incompletáveis (o warn comia o drop). A chamada
                    // explícita viber.vault_add não tem essa desculpa —
                    // typo ("gld") tem de avisar, não criar item à pressa.
                    let known = vault.add_resource(&kind, amount);
                    if !known {
                        if from_collect {
                            vault.item_add(&kind, amount);
                        } else if locals.once_warned.insert(format!("vault_add:{kind}")) {
                            warn!(target: "viber::luau",
                                "viber.vault_add: recurso desconhecido '{kind}' — nada depositado (itens usam viber.item_add)");
                            continue;
                        }
                    }
                    // Evento collect: qualquer entrada no vault (recurso ou
                    // item) chega a `viber.events()` — quests em Lua sem
                    // tocar no Rust.
                    if let Some(events) = locals.events.as_deref_mut() {
                        events.push(ScriptGameEvent::Collect {
                            item: kind.clone(),
                            amount,
                        });
                    }
                    // Snapshot fresco (mesma razão do ItemAdd acima).
                    if let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>() {
                        let key = if known {
                            kind.to_ascii_lowercase()
                        } else {
                            crate::economy::normalize_item(&kind)
                        };
                        *ctx.vault.entry(key).or_insert(0) += amount;
                    }
                }
            }
            ScriptCommand::ItemAdd { id, amount } => {
                if let Some(vault) = vault.as_deref_mut() {
                    vault.item_add(&id, amount);
                    if let Some(events) = locals.events.as_deref_mut() {
                        events.push(ScriptGameEvent::Collect {
                            item: id.clone(),
                            amount,
                        });
                    }
                    // Snapshot do vault atualizado AQUI: a mutação feita por
                    // ESTE sistema não é vista pelo `is_changed()` do frame
                    // seguinte (o tick da mudança coincide com o last_run do
                    // sistema — armadilha clássica de change detection), e o
                    // `viber.vault_get` dos scripts ficava preso no valor do
                    // arranque (correções reutilizáveis → engine).
                    if let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>() {
                        let key = crate::economy::normalize_item(&id);
                        *ctx.vault.entry(key).or_insert(0) += amount;
                    }
                }
            }
            ScriptCommand::Topple { entity } => {
                // tomba na direção herói→entidade (break-style: fall)
                let target_pos =
                    scripts
                        .get(entity)
                        .ok()
                        .and_then(|(_, _, transform, global, _, _)| {
                            global
                                .map(GlobalTransform::translation)
                                .or_else(|| transform.as_ref().map(|t| t.translation))
                        });
                if let (Some(target_pos), Some(player_pos)) = (target_pos, player_pos) {
                    let dir = (target_pos - player_pos).normalize_or_zero();
                    // initial preserva o yaw autoral — sem ele o prop "popeava"
                    // para identidade no 1.º frame da queda.
                    let initial = scripts
                        .get(entity)
                        .ok()
                        .and_then(|(_, _, transform, _, _, _)| {
                            transform.as_ref().map(|t| t.rotation)
                        })
                        .unwrap_or_default();
                    commands
                        .entity(entity)
                        .try_insert(crate::physics_fx::Falling {
                            axis: Vec3::new(dir.z, 0.0, -dir.x),
                            timer: 0.0,
                            initial,
                        });
                    commands.entity(entity).try_remove::<LuaScriptRef>();
                }
            }
            ScriptCommand::PlayCinematic { id, anchor } => {
                commands.queue(move |world: &mut World| {
                    world.write_message(crate::cinematic::PlayCinematic {
                        id,
                        anchor,
                        inline: None,
                    });
                });
            }
            ScriptCommand::SetScale { entity, scale } => {
                if let Ok((_, _, Some(mut transform), _, _, _)) = scripts.get_mut(entity) {
                    transform.scale = Vec3::splat(scale);
                }
            }
            ScriptCommand::EntitySetMaxHp { entity, max } => {
                match locals.healths.get_mut(entity) {
                    Ok((_, mut health, _)) => {
                        health.max = max;
                        health.current = health.current.min(max);
                    }
                    Err(_) => {
                        // Criação: também grava no mapa-sombra (comandos do
                        // mesmo frame não vêem a query).
                        commands
                            .entity(entity)
                            .try_insert(crate::vitals::Health { current: max, max });
                        locals.fresh_health.insert(entity, (max, max));
                    }
                }
            }
            ScriptCommand::EntityDamage { entity, amount } => {
                // Helper local: emitir Kill na TRANSIÇÃO vivo→morto — morte
                // por script EMITE o evento; o resto (cadáver, XP, quests
                // nativas) é do melee. Bater num cadáver não mata outra vez.
                // O nome segue o contrato do melee (`script_kind` do path).
                let kind = scripts
                    .get(entity)
                    .map(|(_, lref, ..)| crate::combat::script_kind(&lref.path))
                    .unwrap_or_else(|_| "creature".to_string());
                macro_rules! kill_if_dead {
                    ($before:expr, $hp:expr) => {
                        if $before > 0.0 && $hp <= 0.0 {
                            if let Some(events) = locals.events.as_deref_mut() {
                                events.push(ScriptGameEvent::Kill {
                                    name: kind.clone(),
                                    entity: entity.to_bits() as i64,
                                });
                            }
                        }
                    };
                }
                if let Ok((_, mut health, _)) = locals.healths.get_mut(entity) {
                    let before = health.current;
                    crate::vitals::apply_damage(&mut health, amount);
                    kill_if_dead!(before, health.current);
                } else if player_components
                    .as_ref()
                    .is_some_and(|(id, _, health, _)| *id == entity && health.is_some())
                {
                    if let Some((_, _, Some(health), _)) = player_components.as_mut() {
                        let before = health.current;
                        crate::vitals::apply_damage(health, amount);
                        kill_if_dead!(before, health.current);
                    }
                } else if let Some((cur, max)) = locals.fresh_health.get_mut(&entity) {
                    let before = *cur;
                    let next = (*cur - amount).max(0.0);
                    *cur = next;
                    commands.entity(entity).try_insert(crate::vitals::Health {
                        current: next,
                        max: *max,
                    });
                    kill_if_dead!(before, next);
                } else if locals.entity_vitals_warned.insert(entity) {
                    warn!(target: "viber::luau",
                        "viber.entity_damage: entidade sem Health — chama viber.entity_set_max_hp primeiro");
                }
            }
            ScriptCommand::EntityHeal { entity, amount } => {
                if let Ok((_, mut health, _)) = locals.healths.get_mut(entity) {
                    health.current = (health.current + amount).min(health.max);
                } else if player_components
                    .as_ref()
                    .is_some_and(|(id, _, health, _)| *id == entity && health.is_some())
                {
                    if let Some((_, _, Some(health), _)) = player_components.as_mut() {
                        health.current = (health.current + amount).min(health.max);
                    }
                } else if let Some((cur, max)) = locals.fresh_health.get_mut(&entity) {
                    let next = (*cur + amount).min(*max);
                    *cur = next;
                    commands.entity(entity).try_insert(crate::vitals::Health {
                        current: next,
                        max: *max,
                    });
                }
            }
            ScriptCommand::VaultTake { kind, amount } => {
                if let Some(vault) = vault.as_deref_mut() {
                    let took = vault.take(&kind, amount);
                    if !took && locals.once_warned.insert(format!("vault_take:{kind}")) {
                        warn!(target: "viber::luau",
                                "viber.vault_take: sem '{kind}' ×{amount} suficiente — nada consumido");
                    }
                    // Snapshot fresco (mesma razão do ItemAdd acima).
                    if took && let Some(mut ctx) = host.lua.app_data_mut::<ScriptCtx>() {
                        let key = crate::economy::normalize_item(&kind);
                        if let Some(left) = ctx.vault.get_mut(&key) {
                            *left = left.saturating_sub(amount);
                        }
                    }
                }
            }
            ScriptCommand::OwnUiAction(name) => {
                if let Some(owners) = locals.ui_action_owners.as_deref_mut() {
                    owners.0.insert(name);
                }
            }
            ScriptCommand::RadialDamage {
                x,
                z,
                radius,
                damage,
                knockback,
            } => {
                // Falloff linear (skills::radial_damage) + knockback radial
                // (physics_fx) + morte com PARIDADE do kill_creature (corpo,
                // XP, quests, evento Kill, SFX).
                let mut kills: Vec<(Entity, String, Vec3)> = Vec::new();
                for (entity, mut health, transform) in locals.healths.iter_mut() {
                    if health.current <= 0.0 {
                        continue;
                    }
                    let pos = transform.translation();
                    let dist = Vec2::new(pos.x - x, pos.z - z).length();
                    let Some(amount) = crate::skills::radial_damage(dist, radius, damage) else {
                        continue;
                    };
                    crate::vitals::apply_damage(&mut health, amount);
                    if knockback > 0.0
                        && let Some(strength) =
                            crate::physics_fx::radial_strength(dist, radius, knockback)
                    {
                        let dir = (pos - Vec3::new(x, pos.y, z)).normalize_or_zero();
                        commands
                            .entity(entity)
                            .try_insert(crate::physics_fx::knockback_after(dir, strength));
                    }
                    if health.current <= 0.0 {
                        let name = scripts
                            .get(entity)
                            .ok()
                            .map(|(_, lref, ..)| crate::combat::script_kind(&lref.path))
                            .unwrap_or_else(|| "creature".to_string());
                        kills.push((entity, name, pos));
                    }
                }
                for (entity, name, _pos) in kills {
                    if let Some(events) = locals.events.as_deref_mut() {
                        events.push(ScriptGameEvent::Kill {
                            name,
                            entity: entity.to_bits() as i64,
                        });
                    }
                }
            }
            ScriptCommand::Burst { preset, pos, count } => {
                locals
                    .fx
                    .burst(&mut commands, &preset, pos, count.clamp(1, 256));
            }
            ScriptCommand::Ring {
                x,
                z,
                radius,
                color,
            } => {
                locals.fx.ring(&mut commands, x, z, radius, color);
            }
            ScriptCommand::Shake(amount) => locals.fx.shake(amount),
            ScriptCommand::CameraKick(impulse) => locals.fx.kick(impulse),
            ScriptCommand::FovKick(deg) => locals.fx.fov_kick(deg),
            ScriptCommand::Punch { stops, bloom } => locals.fx.punch(stops, bloom),
            ScriptCommand::HitStop(secs) => locals.fx.hit_stop(secs),
            ScriptCommand::DamageNumber { text, pos, color } => {
                locals.fx.damage_number(text, pos, color);
            }
            ScriptCommand::StatusClear(kind) => {
                // Reusa o path do status: zerar o veneno limpa o efeito no
                // tick seguinte (o mesmo que o antídoto da hotbar nativa).
                if kind.eq_ignore_ascii_case("venom") {
                    if let Some((p_entity, _, _, _)) = player_components.as_mut() {
                        commands
                            .entity(*p_entity)
                            .try_insert(crate::feedback::StatusEffects {
                                venom: 0.0,
                                venom_tick: 0.0,
                            });
                    }
                } else if locals.status_warned.insert(kind.clone()) {
                    warn!(target: "viber::luau",
                        "viber.status_clear: status desconhecido '{kind}' (válidos: venom)");
                }
            }
            ScriptCommand::OwnSystem(name) => {
                if let Some(owners) = locals.system_owners.as_deref_mut() {
                    owners.0.insert(name);
                }
            }
            ScriptCommand::Say { text, secs } => {
                let duration = if secs > 0.0 {
                    secs
                } else {
                    crate::hud::BALLOON_DURATION
                };
                let shown = crate::quests::show_balloon(
                    &mut locals.balloons,
                    &mut locals.balloon_texts,
                    &text,
                    duration,
                );
                if !shown && !*locals.balloon_warned {
                    *locals.balloon_warned = true;
                    warn!(target: "viber::luau",
                        "viber.say: o mundo não declara <DialogueBalloon> — o texto não aparece no HUD");
                }
            }
            ScriptCommand::SaveNow => {
                if let Some(request) = locals.save_request.as_deref_mut() {
                    request.save = true;
                }
            }
            ScriptCommand::LoadNow => {
                if let Some(request) = locals.save_request.as_deref_mut() {
                    request.load = true;
                }
            }
            ScriptCommand::SpawnPrototype {
                name,
                pos,
                seat,
                on_spawned,
                caller_path,
            } => {
                if let Some(spawns) = locals.spawns.as_deref_mut() {
                    spawns.0.push(crate::recipes::spawn::PendingPrototypeSpawn {
                        name,
                        pos,
                        seat,
                        on_spawned,
                        caller_path,
                    });
                }
            }
            ScriptCommand::RevealBiome(biome) => {
                let biome = biome.trim().to_ascii_lowercase();
                if biome.is_empty() {
                    warn!(target: "viber::luau",
                        "viber.reveal_biome: id de bioma vazio — nada revelado");
                } else if let Some(revealed) = locals.revealed.as_deref() {
                    // Escrita via Commands (o Res aqui é só leitura): a
                    // mudança acende os blips do HUD no mesmo frame
                    // (is_changed no snapshot + landmark_blip_system).
                    let fresh = !revealed.0.contains(&biome);
                    let revealed_biome = biome.clone();
                    commands.queue(move |world: &mut World| {
                        if let Some(mut revealed) =
                            world.get_resource_mut::<crate::travel::RevealedBiomes>()
                        {
                            revealed.0.insert(revealed_biome);
                        }
                    });
                    // O TOAST é do script da atalaia (a apresentação é dela);
                    // aqui fica só o rasto no log.
                    if fresh {
                        info!(target: "viber::nota", "bioma '{biome}' revelado por atalaia");
                    }
                }
            }
            ScriptCommand::SetPose { entity, pos, yaw } => {
                let apply = |transform: &mut Transform| {
                    let scale = transform.scale;
                    transform.translation = pos;
                    transform.rotation = Quat::from_rotation_y(yaw);
                    transform.scale = scale;
                };
                // `players` já está emprestado em `player_components` desde o
                // início do frame — um segundo `get_mut` não compila.
                let mut applied = false;
                if let Some((p_entity, Some(transform), _, _)) = player_components.as_mut()
                    && *p_entity == entity
                {
                    apply(transform);
                    applied = true;
                }
                if !applied {
                    if let Ok((_, _, Some(mut transform), _, _, _)) = scripts.get_mut(entity) {
                        apply(&mut transform);
                    } else if let Ok(mut transform) = locals.poses.get_mut(entity) {
                        apply(&mut transform);
                    }
                }
            }
            ScriptCommand::SoundLoop {
                id,
                clip,
                pitch,
                volume,
                position,
            } => {
                if let Some(loops) = locals.sound_loops.as_deref_mut() {
                    loops.wanted.insert(
                        id,
                        crate::music::ScriptLoopParams {
                            clip,
                            pitch,
                            volume,
                            position,
                        },
                    );
                }
            }
            ScriptCommand::SoundStop(id) => {
                if let Some(loops) = locals.sound_loops.as_deref_mut() {
                    loops.wanted.remove(&id);
                }
            }
            ScriptCommand::MusicLayer(layer) => {
                if let Some(over) = locals.music_layer.as_deref_mut() {
                    over.0 = layer.filter(|name| !name.is_empty());
                }
            }
            ScriptCommand::AddSkillPoints(amount) => {
                commands.queue(move |world: &mut World| {
                    // `LevelState` é COMPONENTE do herói (não resource): um
                    // query curto percorre-o; a árvore é resource.
                    let mut query = world.query::<&mut crate::skills::LevelState>();
                    for mut level in query.iter_mut(world) {
                        level.points += amount;
                    }
                    if let Some(mut tree) = world.get_resource_mut::<crate::skills::SkillTree>() {
                        tree.points += amount;
                    }
                });
            }
        }
    }

    // Compat: `viber.set_position` legado (posição absoluta = MUNDO; o
    // Transform é local, por isso desconta-se o que o pai acrescenta).
    for (entity, pos) in host.take_pending() {
        if let Ok((_, _, Some(mut transform), global, _, _)) = scripts.get_mut(entity) {
            let offset = global
                .map(|g| g.translation() - transform.translation)
                .unwrap_or(Vec3::ZERO);
            transform.translation = pos - offset;
        }
    }
}

/// Aggro-chain (loop 6): ao acertar uma criatura, aliados scriptados a até
/// [`ALERT_RADIUS_M`] recebem `on_player_attack(px, pz)` — os scripts de
/// matilhas usam-no para passar a perseguir.
#[allow(clippy::type_complexity)]
pub fn aggro_alert_system(
    mut alerts: bevy::ecs::message::MessageReader<crate::feedback::AttackAlert>,
    mut host: ResMut<LuaScriptHost>,
    mut scripts: Query<(Entity, &LuaScriptRef, &GlobalTransform), Without<crate::player::Player>>,
) {
    for alert in alerts.read() {
        let alert_pos = alert.position;
        for (entity, lref, transform) in &mut scripts {
            // só quem está perto DO ALVO ATINGIDO (não do player)
            // Early-out por distância quadrada: sqrt por entidade×alerta
            // não compra nada (a comparação é a mesma).
            if transform.translation().distance_squared(alert_pos)
                <= crate::travel::ALERT_RADIUS_M * crate::travel::ALERT_RADIUS_M
                && let Err(error) = host.run_player_attack_alert(
                    entity,
                    &lref.path,
                    transform.translation(),
                    alert.position,
                )
            {
                host.warn_once(&lref.path, &error);
            }
        }
    }
}

/// Hook `on_remove`: drop per-entity leftovers; the chunk stays cached in the
/// registry so a respawned entity reuses the script's existing globals.
pub fn luau_on_remove(
    mut host: ResMut<LuaScriptHost>,
    mut removed: RemovedComponents<LuaScriptRef>,
) {
    for entity in removed.read() {
        host.deactivate(entity);
    }
}
