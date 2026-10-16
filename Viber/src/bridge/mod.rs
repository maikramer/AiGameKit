//! Debug bridge — BRP sobre HTTP (`bevy_remote`) com métodos `viber.*`:
//! screenshots, input sintético, árvore de entidades e ring-buffer de logs.
//! É o equivalente nativo do tooling Chrome DevTools MCP usado no VibeGame.
//!
//! Activar com `viber run --bridge` (porta por omissão: 15702, a porta BRP).
//! Cliente: `viber debug screenshot|click|key|text|move|tree|logs|probe`.

pub mod burst;
pub mod client;
pub mod diff;
pub mod events;
pub mod fork_compat;
pub mod logs;
pub mod lua;
pub mod scene;
pub mod selection;

use std::collections::{BTreeMap, VecDeque};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use base64::Engine as _;
use bevy::ecs::message::MessageWriter;
use bevy::ecs::message::Messages;
use bevy::ecs::system::In;
use bevy::input::keyboard::{Key, KeyCode, KeyboardInput, NativeKey};
use bevy::input::mouse::{MouseButton, MouseButtonInput};
use bevy::input::{ButtonInput, ButtonState};
use bevy::prelude::*;
use bevy::remote::http::RemoteHttpPlugin;
use bevy::remote::{BrpError, BrpResult, RemotePlugin};
use bevy::render::view::screenshot::{Screenshot, save_to_disk};
use bevy::window::{CursorGrabMode, CursorMoved, CursorOptions, PrimaryWindow};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

/// Porta BRP por omissão (a mesma do `bevy_remote`).
pub const DEFAULT_BRIDGE_PORT: u16 = 15702;

pub const METHOD_PING: &str = "viber.ping";
pub const METHOD_SCREENSHOT: &str = "viber.screenshot";
pub const METHOD_SCREENSHOT_STATUS: &str = "viber.screenshot_status";
pub const METHOD_BURST: &str = "viber.burst";
pub const METHOD_BURST_STATUS: &str = "viber.burst_status";
pub const METHOD_TREE: &str = "viber.tree";
pub const METHOD_SCRIPTS_RELOAD: &str = "viber.scripts.reload";
pub const METHOD_LOGS: &str = "viber.logs";
pub const METHOD_PROFILER: &str = "viber.profiler";
pub const METHOD_PROFILER_TAB: &str = "viber.profiler.tab";
pub const METHOD_PROFILER_EXPORT: &str = "viber.profiler.export";
pub const METHOD_PROFILER_EXTRA_TOGGLE: &str = "viber.profiler.extra_toggle";
pub const METHOD_LUA: &str = "viber.lua";
pub const METHOD_KEY: &str = "viber.input.key";
pub const METHOD_TEXT: &str = "viber.input.text";
pub const METHOD_CLICK: &str = "viber.input.click";
pub const METHOD_MOVE: &str = "viber.input.move";
pub const METHOD_RAYCAST: &str = "viber.raycast";
pub const METHOD_CAMERA_ORBIT: &str = "viber.camera.orbit";
pub const METHOD_CAMERA_PAN: &str = "viber.camera.pan";
pub const METHOD_CAMERA_ZOOM: &str = "viber.camera.zoom";
pub const METHOD_CAMERA_LOOK_AT: &str = "viber.camera.look_at";
pub const METHOD_CAMERA_POSE: &str = "viber.camera.pose";
pub const METHOD_PICK: &str = "viber.pick";
pub const METHOD_SELECT: &str = "viber.select";

/// Estado partilhado entre os handlers BRP (PreUpdate) e os sistemas (Update).
#[derive(Resource, Default)]
pub struct BridgeShared {
    pub captures: Arc<Mutex<CaptureStore>>,
    pub bursts: Arc<Mutex<burst::BurstStore>>,
    pub logs: Arc<Mutex<VecDeque<logs::LogEntry>>>,
}

impl BridgeShared {
    pub fn new() -> Self {
        Self {
            captures: Arc::new(Mutex::new(CaptureStore::default())),
            bursts: Arc::new(Mutex::new(burst::BurstStore::default())),
            logs: logs::global_log_buffer(),
        }
    }
}

/// Identidade do mundo servido por esta engine — inserida no boot pelo `run`
/// e devolvida no `viber.ping`. O cliente `viber debug --world` valida-a
/// contra o `engine.json` para apanhar um registo stale (a porta passou a
/// pertencer a outra engine — sem isto, comandos iam para o mundo errado).
#[derive(Resource, Clone)]
pub struct BridgeIdentity {
    pub world: String,
}

/// Um pedido de screenshot em curso: o handler BRP enfileira, o sistema
/// `Update` spawna a captura e o cliente faz polling de `viber.screenshot_status`.
#[derive(Default)]
pub struct CaptureStore {
    next_id: u64,
    pending: Vec<(u64, PathBuf)>,
    captures: BTreeMap<u64, CaptureInfo>,
    /// Entidades `Screenshot` vivas por id de captura — despawnadas quando
    /// a captura termina (ver `process_capture_requests`).
    screenshot_entities: Vec<(u64, Entity)>,
}

#[derive(Clone, Serialize)]
pub struct CaptureInfo {
    pub id: u64,
    /// `pending` | `captured`
    pub status: String,
    pub path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub png_base64: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bytes: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

impl CaptureStore {
    /// Máximo de capturas retidas em memória — cada PNG em base64 pesa
    /// vários MB; sem evicção, uma sessão longa de QA acumulava centenas
    /// de MB no processo da engine. 16 porque sob sessão partilhada
    /// vários agentes fazem poll em paralelo (4 dava `unknown capture id`
    /// flaky quando outro agente capturava entretanto).
    const RETAINED: u64 = 16;

    fn take_pending(&mut self) -> Vec<(u64, PathBuf)> {
        std::mem::take(&mut self.pending)
    }

    fn request(&mut self) -> (u64, PathBuf) {
        self.next_id += 1;
        let id = self.next_id;
        let dir = std::env::temp_dir().join(format!("viber-bridge-{}", std::process::id()));
        if let Err(error) = std::fs::create_dir_all(&dir) {
            warn!(
                "bridge: falha ao criar {} para capturas: {error}",
                dir.display()
            );
        }
        let path = dir.join(format!("shot-{id}.png"));
        let info = CaptureInfo {
            id,
            status: "pending".into(),
            path: path.display().to_string(),
            png_base64: None,
            bytes: None,
            error: None,
        };
        self.captures.insert(id, info);
        self.pending.push((id, path.clone()));
        // Evicção das capturas mais antigas (o PNG no disco fica).
        let cutoff = id.saturating_sub(Self::RETAINED);
        self.captures.retain(|k, _| *k > cutoff);
        (id, path)
    }

    fn get(&mut self, id: u64) -> Option<&CaptureInfo> {
        self.captures.get(&id)
    }

    fn mark_captured(&mut self, id: u64, bytes: usize, png_base64: String) -> Option<CaptureInfo> {
        let info = self.captures.get_mut(&id)?;
        info.status = "captured".into();
        info.bytes = Some(bytes);
        info.png_base64 = Some(png_base64);
        Some(info.clone())
    }
}

/// Motor de debugging remoto: BRP builtin (`world.query`, `world.spawn_entity`,
/// `world.insert_components`, …) + métodos `viber.*`.
pub struct BridgePlugin {
    pub port: u16,
}

impl Plugin for BridgePlugin {
    fn build(&self, app: &mut App) {
        // O `RemoteHttpPlugin` engole o erro de bind (porta ocupada) sem log
        // nenhum — a engine corre "saudável" sem bridge e os `viber debug`
        // batem noutra engine. O listener temporário é largado de imediato
        // (TOCTOU de ms aceitável; o objetivo é o aviso forte no ring-buffer).
        if std::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, self.port)).is_err() {
            error!(
                "bridge: porta {} já ocupada — comandos `viber debug` vão bater noutro processo",
                self.port
            );
        }
        app.insert_resource(BridgeShared::new())
            .add_plugins(
                RemotePlugin::default()
                    .with_method_main(METHOD_PING, ping)
                    .with_method_main("app.info", app_info)
                    .with_method_main(METHOD_SCREENSHOT, screenshot_request)
                    .with_method_main(METHOD_SCRIPTS_RELOAD, scripts_reload)
                    .with_method_main(METHOD_SCREENSHOT_STATUS, screenshot_status)
                    .with_method_main(METHOD_BURST, burst_request)
                    .with_method_main(METHOD_BURST_STATUS, burst_status)
                    .with_method_main(METHOD_TREE, tree)
                    .with_method_main(METHOD_LOGS, logs_method)
                    .with_method_main(METHOD_PROFILER, profiler_snapshot)
                    .with_method_main(METHOD_PROFILER_TAB, profiler_tab)
                    .with_method_main(METHOD_PROFILER_EXPORT, profiler_export)
                    .with_method_main(METHOD_PROFILER_EXTRA_TOGGLE, profiler_extra_toggle)
                    .with_method_main(METHOD_LUA, lua::eval)
                    .with_method_main(METHOD_KEY, input_key)
                    .with_method_main(METHOD_TEXT, input_text)
                    .with_method_main(METHOD_CLICK, input_click)
                    .with_method_main(METHOD_MOVE, input_move)
                    .with_method_main(METHOD_RAYCAST, raycast_method)
                    .with_method_main(METHOD_CAMERA_ORBIT, camera_orbit)
                    .with_method_main(METHOD_CAMERA_PAN, camera_pan)
                    .with_method_main(METHOD_CAMERA_ZOOM, camera_zoom)
                    .with_method_main(METHOD_CAMERA_LOOK_AT, camera_look_at)
                    .with_method_main(METHOD_CAMERA_POSE, camera_pose)
                    .with_method_main(METHOD_PICK, selection::pick_method)
                    .with_method_main(METHOD_SELECT, selection::select_method)
                    .with_method_main(scene::METHOD_LS, scene::ls_method)
                    .with_method_main(scene::METHOD_GET, scene::get_method)
                    .with_method_main(scene::METHOD_FIND, scene::find_method)
                    .with_method_main(scene::METHOD_APPLY, scene::apply_method)
                    .with_method_main(scene::METHOD_UNDO, scene::undo_method)
                    .with_method_main(scene::METHOD_REDO, scene::redo_method)
                    .with_method_main(scene::METHOD_SAVE, scene::save_method)
                    .with_method_main(scene::METHOD_PICK, scene::pick_method)
                    .with_method_main(scene::METHOD_ENTITIES, scene::entities_method)
                    .with_method_main(scene::METHOD_CATALOG, scene::catalog_method)
                    .with_method_main(scene::METHOD_LINT, scene::lint_method)
                    .with_method_main(scene::METHOD_SCHEMA, scene::schema_method)
                    .with_method_main(scene::METHOD_GROUPS, scene::groups_method)
                    .with_method_main(scene::METHOD_CINEMATIC_LIST, scene::cinematic_list_method)
                    .with_method_main(scene::METHOD_CINEMATIC_SHOW, scene::cinematic_show_method)
                    .with_method_main(scene::METHOD_CINEMATIC_PLAY, scene::cinematic_play_method)
                    .with_method_main(scene::METHOD_CINEMATIC_STOP, scene::cinematic_stop_method)
                    .with_method_main(scene::METHOD_CINEMATIC_SHOT, scene::cinematic_shot_method)
                    .with_method_main(scene::METHOD_INSTANCE, scene::instance_method)
                    .with_method_main(scene::METHOD_DROP, scene::drop_method)
                    .with_method_main(scene::METHOD_FILES, scene::files_method)
                    .with_method_main(scene::METHOD_SCENES, scene::scenes_method)
                    .with_method_main(scene::METHOD_NEW_SCENE, scene::new_scene_method)
                    .with_method_main(scene::METHOD_CHANGE, scene::change_method)
                    .with_method_main(scene::METHOD_CURRENT, scene::current_method)
                    .with_method_main(scene::METHOD_TOOL_LIST, scene::tool_list_method)
                    .with_method_main(scene::METHOD_TOOL_SCATTER, scene::tool_scatter_method)
                    .with_method_main(scene::METHOD_TOOL_SETTLE, scene::tool_settle_method)
                    .with_method_main(
                        scene::METHOD_TOOL_CANONICALIZE,
                        scene::tool_canonicalize_method,
                    ),
            )
            .add_plugins(RemoteHttpPlugin::default().with_port(self.port))
            // Shims de compatibilidade com o cliente BRP do Bevy main (fork
            // do ViberEditor/bevy_inspector): entidades "NvG" ↔ bits 0.20.0.
            // DEPOIS do RemotePlugin — substitui entradas do RemoteMethods.
            .add_systems(Update, process_capture_requests)
            .init_resource::<PendingMouseClick>()
            .init_resource::<selection::SelectedEntity>()
            // Event log estruturado: os messages têm de existir (apps mínimas
            // de teste ficam auto-suficientes — add_message é idempotente).
            .add_message::<crate::feedback::PlayerHurt>()
            .add_message::<crate::feedback::DamageNumberEvent>()
            .add_message::<crate::vitals::LevelUpEvent>()
            .add_message::<crate::ui::actions::UiAction>()
            .add_message::<crate::travel::TravelPing>()
            .add_message::<crate::luau::ScriptToast>()
            .init_resource::<events::BridgeEventLog>()
            .init_resource::<FrameStepper>()
            .add_systems(bevy::app::PostUpdate, events::collect_bridge_events)
            .add_systems(bevy::app::PostUpdate, frame_stepper_system)
            // Depois dos sistemas de input (processam mensagens) e ANTES do
            // ui_focus_system (que lê just_pressed e a posição do cursor).
            .add_systems(
                PreUpdate,
                deferred_mouse_release
                    .after(bevy::input::InputSystems)
                    .before(bevy::ui::UiSystems::Focus),
            );
        // Highlight da seleção (`viber.select`) — SÓ com o plugin de gizmos
        // no app (DefaultPlugins com a feature `bevy_gizmos`). Apps mínimas
        // de teste não o têm e o parâmetro `Gizmos` panica na validação.
        if app.is_plugin_added::<bevy::gizmos::GizmoPlugin>() {
            app.add_systems(Update, selection::selection_gizmos);
        }
        // Shims de compatibilidade com o cliente BRP do Bevy main (fork do
        // ViberEditor/bevy_inspector): entidades "NvG" ↔ bits 0.20.0. Tem de
        // ser depois do RemotePlugin — substitui entradas do RemoteMethods.
        fork_compat::install(app);
    }
}

/// QA determinístico frame a frame (`viber.debug.step(n)`): pausa o jogo e
/// avança EXATAMENTE N frames à speed 1. O flag `skip` consome o PostUpdate
/// do frame em que a op aplicou (esse frame já correu com a speed antiga).
#[derive(Debug, Default, Resource)]
pub struct FrameStepper {
    pub remaining: u32,
    pub restore: f32,
    pub skip: bool,
    pub active: bool,
}

/// Consome o orçamento de frames do [`FrameStepper`] (PostUpdate: depois de
/// o frame ter corrido com a speed que o `step` fixou).
fn frame_stepper_system(world: &mut World) {
    let Some(mut stepper) = world.get_resource_mut::<FrameStepper>() else {
        return;
    };
    if !stepper.active {
        return;
    }
    if stepper.skip {
        stepper.skip = false;
        return;
    }
    stepper.remaining = stepper.remaining.saturating_sub(1);
    if stepper.remaining == 0 {
        // Fica PAUSADO (speed 0); `viber.debug.play()` restaura
        // `stepper.restore` — o valor guardado na primeira chamada da cadeia.
        stepper.active = false;
        if let Some(mut base) = world.get_resource_mut::<crate::combat::BaseTimeScale>() {
            base.0 = 0.0;
        }
        world
            .resource_mut::<bevy::time::Time<bevy::time::Virtual>>()
            .set_relative_speed(0.0);
    }
}

/// Clique sintético em curso: o PRESS à espera de injecção e o botão a
/// libertar no frame seguinte — ver [`deferred_mouse_release`].
#[derive(Debug, Default, Resource)]
struct PendingMouseClick {
    press: Option<(f32, f32, MouseButton)>,
    release: Option<MouseButton>,
    /// O press atual é um `press` (arrasto): NÃO solta no frame seguinte —
    /// espera um `state: release` explícito.
    hold: bool,
}

/// Faz o RELEASE de um clique sintético no frame SEGUINTE ao press.
///
/// press + release no MESMO frame eram invisíveis para a UI: o
/// `ui_focus_system` (PreUpdate) marca `Pressed` e, vendo `just_released`,
/// agenda o reset — quando `collect_ui_clicks` (Update) corre, o clique já
/// desapareceu. Com o release um frame depois, `Pressed` sobrevive um Update
/// e o script vê `viber.ui.clicked(id)`.
fn deferred_mouse_release(
    mut pending: ResMut<PendingMouseClick>,
    input: Option<ResMut<ButtonInput<MouseButton>>>,
    mut windows: Query<(Entity, &mut Window, Option<&CursorOptions>), With<PrimaryWindow>>,
    messages: Option<MessageWriter<MouseButtonInput>>,
    cursor_moved: Option<MessageWriter<CursorMoved>>,
    mut window_events: Option<MessageWriter<bevy::window::WindowEvent>>,
) {
    // 1) PRESS injectado AQUI: corre antes do ui_focus_system (PreUpdate) e
    //    depois dos sistemas de input — o focus vê o just_pressed NESTE frame.
    //    Sem janela (headless) as mensagens saem na mesma; só a posição
    //    física do cursor é que não tem onde escrever. Apps sem input/mensagens
    //    (testes mínimos) não têm onde clicar — descarta em silêncio.
    let (Some(mut input), Some(mut messages)) = (input, messages) else {
        pending.press = None;
        pending.release = None;
        return;
    };
    if let Some((x, y, button)) = pending.press.take() {
        // RELEASE atrasado do clique ANTERIOR sai PRIMEIRO (e no mesmo frame):
        // o press tinha prioridade e um único slot de release — cliques rápidos
        // em sequência deixavam o botão antigo "preso" (o release nunca corria)
        if let Some(previous) = pending.release.take() {
            let entity = windows
                .single()
                .map(|(e, _, _)| e)
                .unwrap_or(Entity::PLACEHOLDER);
            let released = MouseButtonInput {
                button: previous,
                state: ButtonState::Released,
                window: entity,
            };
            if let Some(events) = window_events.as_mut() {
                events.write(bevy::window::WindowEvent::MouseButtonInput(released));
            }
            messages.write(released);
            input.release(previous);
        }
        let entity = windows
            .single_mut()
            .map(|(entity, mut window, cursor)| {
                // O warp físico só é possível com o ponteiro CAPTURADO — o
                // Wayland recusa o resto com um ERROR do winit por clique
                // sintético (`cursor position can be set only for locked
                // cursor`). O hover da bevy_ui lê a posição da fila
                // `CursorMoved` (escrita abaixo), por isso o clique sintético
                // continua a funcionar sem o warp.
                if cursor.is_some_and(|c| c.grab_mode == CursorGrabMode::Locked) {
                    window.set_cursor_position(Some(Vec2::new(x, y)));
                }
                entity
            })
            .unwrap_or(Entity::PLACEHOLDER);
        let moved = CursorMoved {
            window: entity,
            position: Vec2::new(x, y),
            delta: None,
        };
        let pressed = MouseButtonInput {
            button,
            state: ButtonState::Pressed,
            window: entity,
        };
        // O picking lê `WindowEvent` (ver `send_cursor`).
        if let Some(events) = window_events.as_mut() {
            events.write(bevy::window::WindowEvent::CursorMoved(moved.clone()));
            events.write(bevy::window::WindowEvent::MouseButtonInput(pressed));
        }
        if let Some(mut cursor_moved) = cursor_moved {
            cursor_moved.write(moved);
        }
        messages.write(pressed);
        input.press(button);
        if std::mem::take(&mut pending.hold) {
            // arrasto: o botão fica em baixo até ao `release` explícito
        } else {
            pending.release = Some(button);
        }
        return;
    }
    // 2) RELEASE no frame seguinte: sem isto, `just_released` no mesmo frame
    //    fazia o focus descartar o Pressed antes de o script o ler.
    if let Some(button) = pending.release.take() {
        let entity = windows
            .single()
            .map(|(e, _, _)| e)
            .unwrap_or(Entity::PLACEHOLDER);
        let released = MouseButtonInput {
            button,
            state: ButtonState::Released,
            window: entity,
        };
        if let Some(events) = window_events.as_mut() {
            events.write(bevy::window::WindowEvent::MouseButtonInput(released));
        }
        messages.write(released);
        input.release(button);
    }
}

/// Sistema `Update`: transforma pedidos de captura pendentes em entidades
/// `Screenshot` com observer `save_to_disk` (o render escreve o PNG; o status
/// passa a `captured` no próximo `viber.screenshot_status`).
fn process_capture_requests(world: &mut World) {
    let requests = {
        let shared = world.resource::<BridgeShared>();
        let mut store = shared
            .captures
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        store.take_pending()
    };
    let mut spawned = Vec::with_capacity(requests.len());
    for (id, path) in requests {
        let entity = world
            .spawn(Screenshot::primary_window())
            .observe(save_to_disk(path))
            .id();
        spawned.push((id, entity));
    }
    let single_shot_spawned = !spawned.is_empty();
    if !spawned.is_empty() {
        let shared = world.resource::<BridgeShared>();
        let mut store = shared
            .captures
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        store.screenshot_entities.extend(spawned);
    }
    // Burst (ver `burst::drive`): no máx UMA captura a mais por frame — o
    // `extract_screenshots` do bevy rejeita (warn + despawn) um 2.º
    // `Screenshot` da mesma janela no mesmo frame, portanto o burst só
    // spawna em frames sem single-shot novo.
    burst::drive(world, single_shot_spawned);
    // Captura terminada (ou já evitada do store) → despawn da entidade
    // `Screenshot`: sem isto cada screenshot deixava uma entidade zombie
    // com observer para sempre.
    let finished: Vec<Entity> = {
        let shared = world.resource::<BridgeShared>();
        let mut store = shared
            .captures
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let mut finished = Vec::new();
        // Snapshot dos ids ainda `pending`: o closure do `retain` não pode
        // ler `store.captures` (o deref do guard captura `*store` inteiro e
        // colide com o borrow mutável de `screenshot_entities`).
        let pending: Vec<u64> = store
            .captures
            .iter()
            .filter(|(_, info)| info.status == "pending")
            .map(|(id, _)| *id)
            .collect();
        store.screenshot_entities.retain(|(id, entity)| {
            if pending.contains(id) {
                true
            } else {
                finished.push(*entity);
                false
            }
        });
        finished
    };
    for entity in finished {
        // O bevy_render já despawnou a entidade `Screenshot` ao consumi-la
        // (1×/captura) — `try_despawn` sem o WARN "Could not despawn entity"
        // a cada `viber debug screenshot` (o QA inteiro rui o log).
        let _ = world.try_despawn(entity);
    }
}

// ---------------------------------------------------------------- helpers

/// PNG completo: assinatura de 8 bytes + chunk terminador IEND no fim
/// (o IEND ocupa os últimos 12 bytes: length 0 + "IEND" + CRC).
fn png_complete(bytes: &[u8]) -> bool {
    bytes.len() >= 12
        && bytes.starts_with(b"\x89PNG\r\n\x1a\n")
        && bytes[bytes.len() - 8..].starts_with(b"IEND")
}

fn parse_params<T: DeserializeOwned>(params: Option<Value>) -> BrpResult<T> {
    let value = params.unwrap_or(Value::Null);
    serde_json::from_value(value).map_err(|error| BrpError {
        code: bevy::remote::error_codes::INVALID_PARAMS,
        message: format!("invalid params: {error}"),
        data: None,
    })
}

fn primary_window(world: &mut World) -> Option<Entity> {
    let mut query = world.query_filtered::<Entity, With<PrimaryWindow>>();
    query.single(world).ok()
}

pub(crate) fn invalid(message: String) -> BrpError {
    BrpError {
        code: bevy::remote::error_codes::INVALID_PARAMS,
        message,
        data: None,
    }
}

// ---------------------------------------------------------------- métodos

fn ping(_params: In<Option<Value>>, world: &mut World) -> BrpResult {
    // Mundo servido — ausente em apps mínimas de teste (e em binários
    // antigos), caso em que o cliente salta a validação de identidade.
    let served = world
        .get_resource::<BridgeIdentity>()
        .map(|identity| identity.world.clone());
    let mut pong = json!({
        "pong": true,
        "version": env!("CARGO_PKG_VERSION"),
        // Identidade do processo: o `session up` compara-a com o pid do filho
        // que spawnou — dois `session up` em corrida escolhem a mesma primeira
        // porta livre, e sem isto o perdedor registava no engine.json a porta
        // da engine do vencedor.
        "pid": std::process::id(),
    });
    if let Some(world_path) = served {
        pong["world"] = json!(world_path);
    }
    Ok(pong)
}

/// `app.info` — o handshake do `bevy_inspector` remoto (Bevy main) antes de
/// pollar `world.query`. O `bevy_remote` 0.20.0 (registry) ainda não traz
/// este builtin; REMOVER quando o Viber subir para um Bevy que o inclua.
/// O `sub_app` é obrigatório no `BrpAppInfoResponse` do fork — sem ele o
/// inspector perde a ligação ("missing field `sub_app`") e a árvore fica
/// vazia.
fn app_info(_params: In<Option<Value>>, _world: &mut World) -> BrpResult {
    Ok(json!({
        "app_name": "viber",
        "bevy_version": "0.20.0",
        "sub_app": "main",
    }))
}

/// Recarrega um script Luau no jogo a correndo — o caminho do dock de scripts
/// do editor (que corre fora do processo). Params: `{"path": "ui/hud.lua"}`.
fn scripts_reload(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    let path = params
        .0
        .as_ref()
        .and_then(|value| value.get("path"))
        .and_then(|value| value.as_str())
        .map(str::to_owned)
        .ok_or_else(|| invalid("scripts.reload: precisa de `path`".into()))?;
    // entidades primeiro (leitura), depois o host mutável
    let entities: Vec<(Entity, Vec3)> = {
        let mut query = world.query::<(Entity, &crate::luau::LuaScriptRef, Option<&Transform>)>();
        query
            .iter(world)
            .filter(|(_, script, _)| script.path == path)
            .map(|(entity, _script, transform)| {
                (
                    entity,
                    transform.map_or(Vec3::ZERO, |transform| transform.translation),
                )
            })
            .collect()
    };
    let Some(mut host) = world.get_resource_mut::<crate::luau::LuaScriptHost>() else {
        return Err(invalid(
            "scripts.reload: sem LuauScriptHost (mundo sem scripts?)".into(),
        ));
    };
    let scripts_dir = host.scripts_dir.clone();
    match crate::hot_reload::reload_script(&mut host, &scripts_dir, &path, &entities) {
        Ok(count) => Ok(json!({ "reloaded": true, "entities": count })),
        Err(error) => Err(invalid(format!("scripts.reload: {error}"))),
    }
}

fn screenshot_request(_params: In<Option<Value>>, world: &mut World) -> BrpResult {
    let (id, path) = {
        let shared = world.resource::<BridgeShared>();
        let mut store = shared
            .captures
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        store.request()
    };
    Ok(json!({ "id": id, "path": path.display().to_string() }))
}

/// `viber.burst` — enfileira um burst de N frames seguidos numa folha
/// 4096×4096 (`{"frames": 4|9|16, "skip": u32, "output": path?}`). O
/// `skip` são frames renderizados entre capturas (`0` = consecutivos).
fn burst_request(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        frames: Option<u32>,
        skip: Option<u32>,
        output: Option<String>,
    }
    let params: Params = parse_params(params.0)?;
    let frames = params.frames.unwrap_or(burst::DEFAULT_FRAMES);
    let skip = params.skip.unwrap_or(0);
    let output = params.output.map(PathBuf::from);
    let (id, path) = {
        let shared = world.resource::<BridgeShared>();
        let mut store = shared
            .bursts
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        store.request(frames, skip, output).map_err(invalid)?
    };
    Ok(json!({
        "id": id,
        "frames": frames,
        "skip": skip,
        "path": path.display().to_string(),
    }))
}

/// `viber.burst_status` — `{"id"}` → estado do burst; com `status`
/// `captured` vem `png_base64` + `bytes` + geometria em `sheet`.
fn burst_status(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        id: u64,
    }
    let params: Params = parse_params(params.0)?;
    let shared = world.resource::<BridgeShared>();
    let store = shared
        .bursts
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let Some(info) = store.get(params.id) else {
        return Err(invalid(format!("unknown burst id {}", params.id)));
    };
    let mut out = json!({
        "id": info.id,
        "status": info.status.as_str(),
        "frames": info.frames,
        "skip": info.skip,
        "path": info.path.display().to_string(),
        "captured": info.collected_count,
        "spawned": info.spawned,
    });
    // Veredicto numérico por frame (luma média/desvio): flicker lê-se nas
    // OSCILAÇÕES entre células sem abrir o PNG.
    let stats: Vec<&burst::FrameStat> = info.frame_stats.iter().flatten().collect();
    if !stats.is_empty() {
        let means: Vec<f32> = stats.iter().map(|s| s.mean).collect();
        let stds: Vec<f32> = stats.iter().map(|s| s.std).collect();
        let mean_of_means = means.iter().sum::<f32>() / means.len() as f32;
        let max_mean_swing = means
            .iter()
            .fold(0.0_f32, |acc, m| acc.max((m - mean_of_means).abs()));
        let max_consecutive_delta = means
            .windows(2)
            .map(|pair| (pair[1] - pair[0]).abs())
            .fold(0.0_f32, f32::max);
        out["frame_stats"] = json!(
            stats
                .iter()
                .map(|s| json!({
                    "index": s.index, "mean": s.mean, "std": s.std,
                }))
                .collect::<Vec<_>>()
        );
        out["flicker"] = json!({
            "max_mean_swing": max_mean_swing,
            "max_consecutive_delta": max_consecutive_delta,
            "std_range": [stds.iter().cloned().fold(f32::MAX, f32::min),
                          stds.iter().cloned().fold(f32::MIN, f32::max)],
        });
    }
    if let Some(result) = &info.result {
        out["bytes"] = json!(result.bytes);
        out["png_base64"] = json!(result.png_base64);
        out["sheet"] = serde_json::to_value(result.layout).expect("serialize");
    }
    if let Some(error) = &info.error {
        out["error"] = json!(error);
    }
    Ok(out)
}

fn screenshot_status(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        id: u64,
    }
    let params: Params = parse_params(params.0)?;
    let shared = world.resource::<BridgeShared>();
    let mut store = shared
        .captures
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let Some(info) = store.get(params.id) else {
        return Err(invalid(format!("unknown capture id {}", params.id)));
    };
    if info.status == "pending" {
        let path = PathBuf::from(&info.path);
        if path.is_file() {
            match std::fs::read(&path) {
                // Só `captured` com PNG COMPLETO: o `save_to_disk` do Bevy
                // escreve com create+write (não atómico) e um poll pode ler
                // o ficheiro a meio — codificar essa leitura selava o estado
                // `captured` com um PNG truncado, para sempre.
                Ok(bytes) if png_complete(&bytes) => {
                    let encoded = base64::engine::general_purpose::STANDARD.encode(&bytes);
                    let info = store
                        .mark_captured(params.id, bytes.len(), encoded)
                        .expect("id existe");
                    return Ok(serde_json::to_value(info).expect("serialize"));
                }
                // PNG ainda a meio da escrita → continua pending; o próximo
                // poll relê. Erro real de leitura → status de erro.
                Ok(_) => {}
                Err(error) => {
                    if let Some(info) = store.captures.get_mut(&params.id) {
                        // status="error" + causa: o cliente sai logo em vez
                        // de fazer spin até timeout sem saber o motivo.
                        info.status = "error".into();
                        info.error = Some(error.to_string());
                    }
                }
            }
        }
    }
    let info = store.get(params.id).expect("id existe").clone();
    Ok(serde_json::to_value(info).expect("serialize"))
}

/// Cast de raio CONTRA A FÍSICA (`viber.raycast`) — a query que as closures
/// Lua não conseguem fazer (o contexto Rapier vive no World). Params:
/// `{x,y,z, dx,dy,dz, max_toi?, solid?}` (dir normalizado dentro; `max_toi`
/// default 100 m). Resposta: `{hit: false}` ou `{hit: true, entity, name?,
/// toi, point, normal}`.
fn raycast_method(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        x: f32,
        y: f32,
        z: f32,
        dx: f32,
        dy: f32,
        dz: f32,
        max_toi: Option<f32>,
        solid: Option<bool>,
    }
    let params: Params = parse_params(params.0)?;
    let dir = Vec3::new(params.dx, params.dy, params.dz);
    if !dir.is_finite() || dir.length_squared() < 1e-12 {
        return Err(invalid("raycast: direção inválida (NaN/inf/zero)".into()));
    }
    raycast_json(
        world,
        Vec3::new(params.x, params.y, params.z),
        dir.normalize(),
        params.max_toi,
        params.solid.unwrap_or(true),
    )
}

/// Núcleo do raycast contra a física — partilhado por `viber.raycast` e
/// `viber.pick` (unproject da câmara de editor). `dir` DEVE estar
/// normalizado. A resposta inclui a raiz nomeada da instância (`root`/`root
/// _name`) — o hit cai no collider-filho anónimo.
pub(crate) fn raycast_json(
    world: &mut World,
    origin: Vec3,
    dir: Vec3,
    max_toi: Option<f32>,
    solid: bool,
) -> BrpResult {
    use bevy_rapier3d::prelude::{
        QueryFilter, RapierContextColliders, RapierContextSimulation, RapierQueryPipeline,
        RapierRigidBodySet,
    };
    use bevy_rapier3d::rapier::parry::query::DefaultQueryDispatcher;

    let max_toi = max_toi.unwrap_or(100.0).max(0.0);
    let Some(ctx_entity) = world
        .iter_entities()
        .find(|e| e.contains::<RapierContextSimulation>())
        .map(|e| e.id())
    else {
        return Err(invalid("sem contexto de física (PhysicsPlugin?)".into()));
    };
    // Tudo por referência imutável: as três Ref coexistem (e o World fica
    // livre para o `get::<Name>` depois).
    let hit = {
        let Some(sim) = world.get::<RapierContextSimulation>(ctx_entity) else {
            return Err(invalid("sem contexto de física (PhysicsPlugin?)".into()));
        };
        let Some(colliders) = world.get::<RapierContextColliders>(ctx_entity) else {
            return Err(invalid("sem colliders de física".into()));
        };
        let Some(bodies) = world.get::<RapierRigidBodySet>(ctx_entity) else {
            return Err(invalid("sem corpos de física".into()));
        };
        RapierQueryPipeline::new_scoped(
            &sim.broad_phase,
            colliders,
            bodies,
            &QueryFilter::default(),
            &DefaultQueryDispatcher,
            |pipeline| pipeline.cast_ray_and_get_normal(origin, dir, max_toi, solid),
        )
    };
    let Some((entity, hit)) = hit else {
        return Ok(json!({ "hit": false }));
    };
    let toi = hit.time_of_impact;
    let normal = hit.normal;
    let (root, root_name) = selection::named_root(world, entity);
    let name = world.get::<Name>(entity).map(|n| n.to_string());
    let point = origin + dir * toi;
    let mut out = json!({
        "hit": true,
        "entity": entity.to_bits(),
        "root": fork_compat::entity_bits_to_string(root.to_bits())
            .map(Value::String)
            .unwrap_or_else(|| json!(root.to_bits())),
        "toi": toi,
        "point": [point.x, point.y, point.z],
        "normal": [normal.x, normal.y, normal.z],
    });
    if let Some(name) = name {
        out["name"] = json!(name);
    }
    if let Some(root_name) = root_name {
        out["root_name"] = json!(root_name);
    }
    Ok(out)
}

/// Árvore de entidades — o "a11y snapshot" do bridge: id, nome, pai,
/// translation e lista de componentes (nomes reflectidos).
fn tree(_params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Serialize)]
    struct EntityNode {
        id: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        name: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        parent: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        translation: Option<[f32; 3]>,
        components: Vec<String>,
    }

    let mut nodes = Vec::new();
    for entity in world.iter_entities() {
        let components: Vec<String> = entity
            .archetype()
            .components()
            .iter()
            .filter_map(|id| world.components().get_info(*id))
            .map(|info| info.name().to_string())
            .collect();
        nodes.push(EntityNode {
            id: entity.id().to_string(),
            name: entity.get::<Name>().map(|n| n.to_string()),
            parent: entity
                .get::<ChildOf>()
                .map(|child_of| child_of.0.to_string()),
            translation: entity
                .get::<Transform>()
                .map(|t| [t.translation.x, t.translation.y, t.translation.z]),
            components,
        });
    }
    Ok(serde_json::to_value(nodes).expect("serialize"))
}

fn logs_method(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        limit: Option<usize>,
    }
    let params: Params = parse_params(params.0)?;
    let shared = world.resource::<BridgeShared>();
    // Clonar as entradas e LARGAR o lock antes de serializar: serializar sob
    // o mutex global fazia stall de todos os threads que logam no processo.
    let entries: Vec<logs::LogEntry> = {
        let logs = shared
            .logs
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        // O limite nunca excede a capacidade do buffer — pedidos absurdos
        // devolvem tudo o que há, sem alocar além do buffer.
        let limit = params.limit.unwrap_or(100).min(logs.capacity());
        let start = logs.len().saturating_sub(limit);
        logs.iter().skip(start).cloned().collect()
    };
    Ok(serde_json::to_value(entries).expect("serialize"))
}

/// Snapshot do profiler (fps/frame-time/entidades/scripts ativos) — o mesmo
/// corpo do overlay F3, para QA headless (`viber debug prof`).
fn profiler_snapshot(_params: In<Option<Value>>, world: &mut World) -> BrpResult {
    Ok(crate::profiler::snapshot(world))
}

/// Snapshot rico de um tab do profiler (`viber.profiler.tab`):
/// `{"tab": "systems|world|physics|audio|extras|all"}`. `all` devolve todos
/// (o mesmo payload do export, sem escrever ficheiro).
fn profiler_tab(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    let tab = params
        .0
        .as_ref()
        .and_then(|p| p.get("tab"))
        .and_then(Value::as_str)
        .unwrap_or("systems")
        .to_string();
    if tab == "all" {
        // O MESMO JSON do botão COPIAR / do ficheiro de export.
        return Ok(crate::profiler::full_snapshot(world));
    }
    if tab == "extras" {
        return Ok(json!({ "extras": crate::profiler::extras_snapshot(world) }));
    }
    Ok(crate::profiler::tab_snapshot(world, &tab))
}

/// Exporta o snapshot completo do profiler para ficheiro
/// (`viber.profiler.export`): `{"path": "…"}` opcional; devolve
/// `{"path", "bytes"}`.
fn profiler_export(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    let path: Option<PathBuf> = match params.0.as_ref().and_then(|p| p.get("path")) {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) => Some(PathBuf::from(s)),
        Some(_) => return Err(invalid("`path` tem de ser string".into())),
    };
    match crate::profiler::export_to_file(world, path) {
        Ok(path) => {
            let bytes = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
            Ok(json!({ "path": path.display().to_string(), "bytes": bytes }))
        }
        Err(error) => Err(invalid(format!("export falhou: {error}"))),
    }
}

/// Alterna um extra do profiler (`viber.profiler.extra_toggle`):
/// `{"id": "colliders"}` → `{"id", "on"}`.
fn profiler_extra_toggle(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(serde::Deserialize)]
    struct Params {
        id: String,
    }
    let params: Params = parse_params(params.0)?;
    match crate::profiler::toggle_extra(world, &params.id) {
        Some(on) => Ok(json!({ "id": params.id, "on": on })),
        None => Err(invalid(format!("extra desconhecido: {}", params.id))),
    }
}

// ------------------------------------------------------- câmara de editor

/// Erro padrão dos métodos `viber.camera.*` fora do modo `--camera free` —
/// o cliente do editor distingue "modo errado" de "params inválidos".
fn editor_cam_locked() -> BrpError {
    invalid("camera mode is not free — arranca a engine com `viber run --camera free`".into())
}

/// Extrai o estado da câmara de editor ACTIVO (recurso presente e `active`),
/// ou o erro claro de modo. As mutações BRP aplicam no frame seguinte
/// (`editor_camera_follow` projecta o estado no `Transform`).
fn require_editor_cam(
    world: &mut World,
) -> Result<bevy::ecs::change_detection::Mut<'_, crate::editor_camera::EditorCamState>, BrpError> {
    world
        .get_resource_mut::<crate::editor_camera::EditorCamState>()
        .filter(|state| state.active)
        .ok_or_else(editor_cam_locked)
}

/// Pose como JSON (`target`/`yaw`/`pitch`/`distance`) — corpo comum das
/// respostas `viber.camera.*`, para o editor sincronizar UI/minimapa.
fn editor_cam_pose(state: &crate::editor_camera::EditorCamState) -> Value {
    json!({
        "target": [state.target.x, state.target.y, state.target.z],
        "yaw": state.yaw_deg,
        "pitch": state.pitch_deg,
        "distance": state.distance,
    })
}

/// `viber.camera.orbit {dx_deg?, dy_deg?}` — roda a órbita da câmara de
/// editor em torno do alvo (`yaw += dx`, pitch clamp -10°..85°). Devolve a
/// pose resultante.
fn camera_orbit(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        dx_deg: Option<f32>,
        dy_deg: Option<f32>,
    }
    let params: Params = parse_params(params.0)?;
    let mut state = require_editor_cam(world)?;
    crate::editor_camera::orbit(
        &mut state,
        params.dx_deg.unwrap_or(0.0),
        params.dy_deg.unwrap_or(0.0),
    )
    .map_err(invalid)?;
    Ok(editor_cam_pose(&state))
}

/// `viber.camera.pan {dx?, dy?}` — pan no plano da câmara a partir de
/// deltas de rato em PÍXEIS do preview do editor (escala com a distância ao
/// alvo; o conteúdo segue o cursor). Devolve a pose resultante.
fn camera_pan(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        dx: Option<f32>,
        dy: Option<f32>,
    }
    let params: Params = parse_params(params.0)?;
    let mut state = require_editor_cam(world)?;
    crate::editor_camera::pan(
        &mut state,
        params.dx.unwrap_or(0.0),
        params.dy.unwrap_or(0.0),
    )
    .map_err(invalid)?;
    Ok(editor_cam_pose(&state))
}

/// `viber.camera.zoom {factor}` — `distance /= factor` com clamp 5..3000
/// (`factor` 2 aproxima à metade). Devolve a pose resultante.
fn camera_zoom(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        factor: f32,
    }
    let params: Params = parse_params(params.0)?;
    let mut state = require_editor_cam(world)?;
    crate::editor_camera::zoom(&mut state, params.factor).map_err(invalid)?;
    Ok(editor_cam_pose(&state))
}

/// `viber.camera.look_at {x, y, z}` — aponta a órbita a um novo alvo (ex.:
/// clique no minimapa do editor). Devolve a pose resultante.
fn camera_look_at(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        x: f32,
        y: f32,
        z: f32,
    }
    let params: Params = parse_params(params.0)?;
    let mut state = require_editor_cam(world)?;
    crate::editor_camera::look_at(&mut state, Vec3::new(params.x, params.y, params.z))
        .map_err(invalid)?;
    Ok(editor_cam_pose(&state))
}

/// `viber.camera.pose {}` — leitura da pose actual (target/yaw/pitch/
/// distance), para o editor sincronizar UI/minimapa após interações
/// concorrentes.
fn camera_pose(_params: In<Option<Value>>, world: &mut World) -> BrpResult {
    let state = require_editor_cam(world)?;
    Ok(editor_cam_pose(&state))
}

// ---------------------------------------------------------------- input

/// Envia um `KeyboardInput` sintético + actualiza `ButtonInput<KeyCode>`.
fn send_key(world: &mut World, key_code: KeyCode, state: ButtonState, text: Option<String>) {
    let window = primary_window(world).unwrap_or(Entity::PLACEHOLDER);
    let logical_key = match (&text, key_code) {
        (Some(t), _) => Key::Character(t.as_str().into()),
        (None, KeyCode::Space) => Key::Space,
        (None, KeyCode::Enter) => Key::Enter,
        (None, KeyCode::Escape) => Key::Escape,
        (None, KeyCode::Tab) => Key::Tab,
        _ => Key::Unidentified(NativeKey::Unidentified),
    };
    let text: Option<_> = text.map(|t| t.into());
    // No-op silencioso sem os recursos (app de teste mínimo, sem
    // InputPlugin) — mesma defesa do `deferred_mouse_release`.
    if let Some(mut messages) = world.get_resource_mut::<Messages<KeyboardInput>>() {
        messages.write(KeyboardInput {
            key_code,
            logical_key,
            state,
            text,
            window,
            repeat: false,
        });
    }
    if let Some(mut input) = world.get_resource_mut::<ButtonInput<KeyCode>>() {
        match state {
            ButtonState::Pressed => input.press(key_code),
            ButtonState::Released => input.release(key_code),
        }
    }
}

fn send_cursor(world: &mut World, position: Vec2) {
    let window = primary_window(world).unwrap_or(Entity::PLACEHOLDER);
    let moved = CursorMoved {
        window,
        position,
        delta: None,
    };
    // O `bevy_picking` (botões feathers, linhas da árvore, splitters…) lê
    // `WindowEvent`, não `CursorMoved` — o winit escreve os dois.
    if let Some(mut messages) = world.get_resource_mut::<Messages<bevy::window::WindowEvent>>() {
        messages.write(bevy::window::WindowEvent::CursorMoved(moved.clone()));
    }
    if let Some(mut messages) = world.get_resource_mut::<Messages<CursorMoved>>() {
        messages.write(moved);
    }
    // O hover/clique da bevy_ui lê a posição do cursor NA WINDOW (winit),
    // não a fila de `CursorMoved` — sem escrever aqui, um clique sintético
    // nunca "estava" sobre o nó em causa e os botões da UI não reagiam.
    // Warp físico exige o ponteiro capturado (Wayland: ERROR do winit
    // caso contrário — ver o outro sítio em `input_move`); o hover da UI
    // continua a funcionar pelas mensagens acima.
    let locked = world
        .get::<CursorOptions>(window)
        .is_some_and(|c| c.grab_mode == CursorGrabMode::Locked);
    if locked && let Some(mut window) = world.get_mut::<Window>(window) {
        window.set_cursor_position(Some(position));
    }
}

fn input_key(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        /// Nome da variante `KeyCode` serde (ex.: `KeyW`, `Space`, `ArrowUp`).
        key: KeyCode,
        /// `click` (omissão) | `press` | `release`
        state: Option<String>,
        /// Texto do keypress (preenche `logical_key`/`text` do evento).
        text: Option<String>,
        /// Com `click`, envolve o char num Shift sintético (para maiúsculas).
        shift: Option<bool>,
    }
    let params: Params = parse_params(params.0)?;
    let state = params.state.as_deref().unwrap_or("click");
    // Estado desconhecido era aceite em silêncio: nada era enviado mas a
    // resposta dizia {"sent": "foo"} — QA achava que a tecla tinha chegado.
    if !matches!(state, "click" | "press" | "release") {
        return Err(invalid(format!(
            "invalid params: state `{state}` — esperado click | press | release"
        )));
    }
    let press = matches!(state, "click" | "press");
    let release = matches!(state, "click" | "release");
    if params.shift == Some(true) {
        send_key(world, KeyCode::ShiftLeft, ButtonState::Pressed, None);
    }
    if press {
        send_key(world, params.key, ButtonState::Pressed, params.text.clone());
    }
    if release {
        send_key(world, params.key, ButtonState::Released, params.text);
    }
    if params.shift == Some(true) {
        send_key(world, KeyCode::ShiftLeft, ButtonState::Released, None);
    }
    Ok(json!({ "sent": state }))
}

fn input_text(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        text: String,
    }
    let params: Params = parse_params(params.0)?;
    // Cada char vira 2-4 key events no mesmo frame — texto gigante
    // congelava a engine; rejeitar cedo com invalid params.
    if params.text.chars().count() > 4096 {
        return Err(invalid("text demasiado longo (cap 4096 chars)".into()));
    }
    for char in params.text.chars() {
        let (key_code, shift) = keycode_for_char(char);
        if shift {
            send_key(world, KeyCode::ShiftLeft, ButtonState::Pressed, None);
        }
        send_key(
            world,
            key_code,
            ButtonState::Pressed,
            Some(char.to_string()),
        );
        send_key(
            world,
            key_code,
            ButtonState::Released,
            Some(char.to_string()),
        );
        if shift {
            send_key(world, KeyCode::ShiftLeft, ButtonState::Released, None);
        }
    }
    Ok(json!({ "chars": params.text.chars().count() }))
}

fn input_click(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        x: f32,
        y: f32,
        button: Option<MouseButton>,
        /// `click` (omissão) | `press` | `release`
        state: Option<String>,
    }
    let params: Params = parse_params(params.0)?;
    let button = params.button.unwrap_or(MouseButton::Left);
    let state = params.state.as_deref().unwrap_or("click");
    match state {
        // O clique INTEIRO é injectado no PreUpdate do frame seguinte (ver
        // `deferred_mouse_release`) — no meio do frame, focus/input já correram.
        // `press` = arrasto: sem release automático (`viber debug drag`).
        "click" | "press" => {
            let mut pending = world.resource_mut::<PendingMouseClick>();
            pending.press = Some((params.x, params.y, button));
            pending.hold = state == "press";
        }
        "release" => {
            send_cursor(world, Vec2::new(params.x, params.y));
            world.resource_mut::<PendingMouseClick>().release = Some(button);
        }
        other => {
            return Err(invalid(format!(
                "invalid params: state `{other}` — esperado click | press | release"
            )));
        }
    }
    Ok(json!({ "x": params.x, "y": params.y, "button": format!("{button:?}"), "state": state }))
}

fn input_move(params: In<Option<Value>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        x: f32,
        y: f32,
    }
    let params: Params = parse_params(params.0)?;
    send_cursor(world, Vec2::new(params.x, params.y));
    Ok(json!({ "x": params.x, "y": params.y }))
}

/// KeyCode + shift para um char (alfanumérico e espaço; restantes chars
/// chegam como `Unidentified` com o campo `text` preenchido).
fn keycode_for_char(char: char) -> (KeyCode, bool) {
    if let Some(digit) = char.to_digit(10) {
        let variant = match digit {
            0 => KeyCode::Digit0,
            1 => KeyCode::Digit1,
            2 => KeyCode::Digit2,
            3 => KeyCode::Digit3,
            4 => KeyCode::Digit4,
            5 => KeyCode::Digit5,
            6 => KeyCode::Digit6,
            7 => KeyCode::Digit7,
            8 => KeyCode::Digit8,
            _ => KeyCode::Digit9,
        };
        return (variant, false);
    }
    if char.is_ascii_uppercase() {
        return (letter_keycode(char.to_ascii_lowercase()), true);
    }
    if char.is_ascii_lowercase() {
        return (letter_keycode(char), false);
    }
    if char == ' ' {
        return (KeyCode::Space, false);
    }
    match char {
        '!' => (KeyCode::Digit1, true),
        '@' => (KeyCode::Digit2, true),
        '#' => (KeyCode::Digit3, true),
        '$' => (KeyCode::Digit4, true),
        '%' => (KeyCode::Digit5, true),
        '^' => (KeyCode::Digit6, true),
        '&' => (KeyCode::Digit7, true),
        '*' => (KeyCode::Digit8, true),
        '(' => (KeyCode::Digit9, true),
        ')' => (KeyCode::Digit0, true),
        '-' => (KeyCode::Minus, false),
        '_' => (KeyCode::Minus, true),
        '=' => (KeyCode::Equal, false),
        '+' => (KeyCode::Equal, true),
        '.' => (KeyCode::Period, false),
        ',' => (KeyCode::Comma, false),
        '/' => (KeyCode::Slash, false),
        ';' => (KeyCode::Semicolon, false),
        '\'' => (KeyCode::Quote, false),
        '\n' | '\r' => (KeyCode::Enter, false),
        '\t' => (KeyCode::Tab, false),
        _ => (
            KeyCode::Unidentified(bevy::input::keyboard::NativeKeyCode::Unidentified),
            false,
        ),
    }
}

fn letter_keycode(char: char) -> KeyCode {
    match char {
        'a' => KeyCode::KeyA,
        'b' => KeyCode::KeyB,
        'c' => KeyCode::KeyC,
        'd' => KeyCode::KeyD,
        'e' => KeyCode::KeyE,
        'f' => KeyCode::KeyF,
        'g' => KeyCode::KeyG,
        'h' => KeyCode::KeyH,
        'i' => KeyCode::KeyI,
        'j' => KeyCode::KeyJ,
        'k' => KeyCode::KeyK,
        'l' => KeyCode::KeyL,
        'm' => KeyCode::KeyM,
        'n' => KeyCode::KeyN,
        'o' => KeyCode::KeyO,
        'p' => KeyCode::KeyP,
        'q' => KeyCode::KeyQ,
        'r' => KeyCode::KeyR,
        's' => KeyCode::KeyS,
        't' => KeyCode::KeyT,
        'u' => KeyCode::KeyU,
        'v' => KeyCode::KeyV,
        'w' => KeyCode::KeyW,
        'x' => KeyCode::KeyX,
        'y' => KeyCode::KeyY,
        _ => KeyCode::KeyZ,
    }
}

#[cfg(test)]
mod png_tests {
    use super::png_complete;

    #[test]
    fn test_png_complete_needs_iend_no_fim() {
        let mut png = b"\x89PNG\r\n\x1a\n".to_vec();
        assert!(!png_complete(&png), "só a assinatura é incompleto");
        png.extend_from_slice(b"\x00\x00\x00\x0dIHDR....");
        assert!(!png_complete(&png), "sem IEND é incompleto");
        png.extend_from_slice(&[0, 0, 0, 0]);
        png.extend_from_slice(b"IEND");
        png.extend_from_slice(&[0xae, 0x42, 0x60, 0x82]);
        assert!(png_complete(&png), "IEND + CRC no fim = completo");
        png.truncate(png.len() - 1);
        assert!(!png_complete(&png), "cortado a meio do CRC é incompleto");
        assert!(!png_complete(b""), "vazio é incompleto");
    }
}

#[cfg(test)]
mod camera_method_tests {
    use super::*;
    use crate::editor_camera::{DEFAULT_DISTANCE, DEFAULT_PITCH_DEG, EditorCamState};

    fn world_with(active: bool) -> World {
        let mut world = World::default();
        if active {
            world.insert_resource(EditorCamState {
                active: true,
                ..EditorCamState::default()
            });
        }
        world
    }

    #[test]
    fn test_camera_methods_require_free_mode() {
        // Estado presente mas INACTIVO (apps mínimas): erro claro de modo.
        let mut world = world_with(false);
        let error = camera_orbit(In(Some(json!({ "dx_deg": 10.0 }))), &mut world).unwrap_err();
        assert!(error.message.contains("not free"), "{}", error.message);
        // Recurso AUSENTE (engine arrancada sem `--camera free`): idem.
        let mut world = World::default();
        let error = camera_pose(In(None), &mut world).unwrap_err();
        assert!(error.message.contains("not free"), "{}", error.message);
    }

    #[test]
    fn test_camera_orbit_handler_applies_and_returns_pose() {
        let mut world = world_with(true);
        let out = camera_orbit(
            In(Some(json!({ "dx_deg": 90.0, "dy_deg": 10.0 }))),
            &mut world,
        )
        .unwrap();
        assert_eq!(out["yaw"], json!(90.0));
        assert_eq!(out["pitch"], json!(DEFAULT_PITCH_DEG + 10.0));
        assert_eq!(out["distance"], json!(DEFAULT_DISTANCE));
        assert_eq!(out["target"], json!([0.0, 0.0, 0.0]));
        // O estado do MUNDO (a fonte do `editor_camera_follow`) também mudou.
        assert_eq!(
            world.resource::<EditorCamState>().yaw_deg,
            90.0,
            "o handler muta o recurso, não só a resposta"
        );
        // Omisso = 0 (delta neutro): só o que foi pedido se move.
        let out = camera_orbit(In(Some(json!({ "dy_deg": -500.0 }))), &mut world).unwrap();
        assert_eq!(out["yaw"], json!(90.0));
        assert_eq!(out["pitch"], json!(crate::editor_camera::MIN_PITCH_DEG));
    }

    #[test]
    fn test_camera_zoom_look_at_pose_roundtrip() {
        let mut world = world_with(true);
        // Zoom inválido: params error (não o erro de modo).
        let error = camera_zoom(In(Some(json!({ "factor": 0.0 }))), &mut world).unwrap_err();
        assert!(error.message.contains("factor"), "{}", error.message);
        let out = camera_zoom(In(Some(json!({ "factor": 2.0 }))), &mut world).unwrap();
        assert_eq!(out["distance"], json!(DEFAULT_DISTANCE / 2.0));
        // look_at + pose: o que se escreve é o que se lê.
        let written = camera_look_at(
            In(Some(json!({ "x": 120.0, "y": 8.0, "z": -40.0 }))),
            &mut world,
        )
        .unwrap();
        let read = camera_pose(In(None), &mut world).unwrap();
        assert_eq!(written["target"], json!([120.0, 8.0, -40.0]));
        assert_eq!(read["target"], json!([120.0, 8.0, -40.0]));
        assert_eq!(read["distance"], json!(DEFAULT_DISTANCE / 2.0));
    }
}

#[cfg(test)]
mod tests;
