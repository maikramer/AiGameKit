//! Troca de cena in-process (o `SceneManager.LoadScene` do Unity / `change_scene` do Godot).
//!
//! Uma cena ([`crate::project`]) é um universo próprio: as entidades, o terreno, a navmesh e
//! a física dela. Trocar de cena = fade a preto → **descarregar** a atual → **carregar** a
//! próxima pelo MESMO caminho do arranque (`spawn::startup` + `terrain::runtime::bootstrap`
//! + nav, agora também no schedule [`SceneLoad`]) → pôr o jogador na entrada → fade de volta.
//!
//! **O que sobrevive** (marcado [`Persistent`]): os autoloads do projeto (jogador, câmara,
//! HUD/UI — `project.autoload`), as entidades globais da engine nascidas no arranque, a VM
//! Lua e o `viber.game()`, quests, cofre, relógio do dia. **O que morre** no varrimento
//! ([`unload_scene`]): toda a raiz (com `Transform`, sem pai) que a cena spawnou —
//! marcada [`SceneEntity`] no carregamento, ou nascida depois de a cena acabar de carregar
//! (projéteis, loot, partículas, protótipos) — e os recursos por cena ([`reset_scene_resources`]).
//!
//! Um mundo fora de um projeto declarado (`project:` no config) tem uma só cena: nada disto
//! corre, o arranque é exatamente o de sempre.

use std::collections::HashSet;
use std::path::PathBuf;

use anyhow::{Result, anyhow, bail};
use bevy::ecs::change_detection::Tick;
use bevy::ecs::query::SpawnDetails;
use bevy::ecs::schedule::ScheduleLabel;
use bevy::prelude::*;
use serde::Serialize;

use crate::player::Player;
use crate::project::{MAIN_SCENE, Project};

/// Entidade (raiz) que pertence à cena carregada: morre no próximo descarregamento.
#[derive(Component, Debug, Default, Clone, Copy)]
pub struct SceneEntity;

/// Entidade que sobrevive a trocas de cena (autoloads, câmara de editor, sonda IBL…).
#[derive(Component, Debug, Default, Clone, Copy)]
pub struct Persistent;

/// O schedule que (re)carrega uma cena: os mesmos sistemas do `Startup` que montam o mundo.
#[derive(ScheduleLabel, Debug, Clone, PartialEq, Eq, Hash)]
pub struct SceneLoad;

/// Conta as cenas carregadas (sistemas "uma vez por cena" comparam com o seu `Local`).
#[derive(Resource, Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct SceneEpoch(pub u64);

/// Os fragmentos autoload do projeto, consumidos UMA vez pelo `spawn::startup` antes da
/// primeira cena.
#[derive(Resource)]
pub struct PendingAutoload(pub Vec<crate::recipes::ParsedWorld>);

/// `<SceneEntry name>` (e cada `<ScenePortal>`): ponto de chegada nomeado.
#[derive(Component, Debug, Clone)]
pub struct SceneEntryPoint {
    pub name: String,
    /// Deslocamento local aplicado à chegada (`arrive-offset` dos portais).
    pub offset: Vec3,
}

/// `<ScenePortal>`: dados autorados.
#[derive(Debug, Clone, PartialEq)]
pub struct ScenePortalSpec {
    /// Id da cena alvo (`main`, `interiors/chapel`).
    pub target: String,
    /// Entrada na cena alvo; `@back` = o portal por onde se entrou; `None` = a primeira.
    pub entry: Option<String>,
    pub prompt: String,
    pub key: String,
    /// Alcance da interação (m, autorado — escalado como os outros).
    pub radius: f32,
    /// Atravessa sozinho a esta distância (sem tecla).
    pub auto_radius: Option<f32>,
    pub sound: Option<String>,
    pub toast: Option<String>,
    pub arrive_offset: [f32; 3],
}

impl Default for ScenePortalSpec {
    fn default() -> Self {
        Self {
            target: String::new(),
            entry: None,
            prompt: "Entrar".into(),
            key: "e".into(),
            radius: 2.8,
            auto_radius: None,
            sound: None,
            toast: None,
            arrive_offset: [0.0; 3],
        }
    }
}

/// Componente do portal vivo.
#[derive(Component, Debug, Clone)]
pub struct ScenePortal(pub ScenePortalSpec);

/// Como o jogador chega à cena nova.
#[derive(Debug, Clone, PartialEq)]
pub enum Arrival {
    /// Entrada nomeada (`None` = a primeira `<SceneEntry>`, senão o primeiro portal).
    Entry(Option<String>),
    /// O portal por onde se entrou na cena que se deixa.
    Back,
    /// Posição exata (saves).
    Position(Vec3),
    /// Não mexe no jogador (editor).
    Keep,
}

impl Arrival {
    /// `entry="@back"` → [`Arrival::Back`]; outro texto → entrada nomeada.
    pub fn from_entry(entry: Option<&str>) -> Self {
        match entry.map(str::trim).filter(|e| !e.is_empty()) {
            Some("@back") => Arrival::Back,
            other => Arrival::Entry(other.map(str::to_owned)),
        }
    }
}

/// Pedido de troca.
#[derive(Debug, Clone)]
pub struct ChangeScene {
    pub scene: String,
    pub arrival: Arrival,
    /// O portal que pediu a troca (para o `@back` do regresso).
    pub via_portal: Option<String>,
    pub toast: Option<String>,
    pub sound: Option<String>,
    /// Repõe o `came_from` da cena nova (load de um save) em vez de o derivar do portal.
    pub came_from: Option<(String, String)>,
    /// Pose do herói AO PEDIR (preenchida pelo [`request_change`] quando há portal).
    pub return_pose: Option<ReturnPose>,
}

impl ChangeScene {
    pub fn new(scene: impl Into<String>, arrival: Arrival) -> Self {
        Self {
            scene: scene.into(),
            arrival,
            via_portal: None,
            toast: None,
            sound: None,
            came_from: None,
            return_pose: None,
        }
    }
}

/// Onde o herói estava (e para onde olhava a câmara) quando saiu de uma cena por um
/// portal — o `@back` devolve-o exatamente aí, de costas para a porta e com a câmara do
/// lado de fora (chegar EM CIMA da porta punha a câmara dentro do edifício).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ReturnPose {
    pub position: Vec3,
    pub rotation: Quat,
    pub camera_yaw_deg: f32,
}

/// A cena carregada.
#[derive(Resource, Debug, Clone, Serialize)]
pub struct CurrentScene {
    pub root: PathBuf,
    /// Id (`main`, `interiors/chapel`); `None` = mundo fora de um projeto declarado.
    pub id: Option<String>,
    pub path: PathBuf,
    pub title: Option<String>,
    /// A entrada por onde se chegou.
    pub entry: Option<String>,
    /// `(cena, portal)` por onde se entrou nesta cena — o alvo do `@back`.
    pub came_from: Option<(String, String)>,
    /// A pose do herói na cena de onde veio (ver [`ReturnPose`]).
    #[serde(skip)]
    pub return_pose: Option<ReturnPose>,
}

impl CurrentScene {
    /// A cena de arranque de `path`.
    pub fn for_path(path: &std::path::Path, title: Option<String>) -> Self {
        let project = Project::discover(path);
        let id = if project.declared {
            project.scene_id(path)
        } else {
            None
        };
        Self {
            root: project.root,
            id,
            path: path.to_path_buf(),
            title,
            entry: None,
            came_from: None,
            return_pose: None,
        }
    }

    /// Pode trocar de cena (projeto declarado e esta é uma cena dele).
    pub fn switchable(&self) -> bool {
        self.id.is_some()
    }
}

/// Ids das cenas do projeto (barato: só nomes de ficheiro) — `viber.change_scene` valida
/// contra ele sem tocar no disco.
#[derive(Resource, Debug, Default, Clone)]
pub struct SceneIndex {
    pub ids: Vec<String>,
}

impl SceneIndex {
    pub fn of(current: &CurrentScene) -> Self {
        if !current.switchable() {
            return Self::default();
        }
        let project = Project::at(&current.root);
        let ids = project
            .scene_files()
            .iter()
            .filter_map(|path| project.scene_id(path))
            .collect();
        Self { ids }
    }
}

/// Fase da troca.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LoaderPhase {
    #[default]
    Idle,
    FadeOut,
    Swap,
    Settle,
    FadeIn,
}

/// O que o último varrimento apagou (debug: `viber.scene.current`).
#[derive(Debug, Clone, Default, Serialize)]
pub struct SweepReport {
    pub roots: usize,
    pub entities_before: usize,
    pub entities_after: usize,
    /// Os nomes mais comuns entre as raízes apagadas (prefixo até ao 1.º espaço/ponto).
    pub top: Vec<(String, usize)>,
}

/// Máquina da troca de cena.
#[derive(Resource, Debug, Default)]
pub struct SceneLoader {
    pub phase: LoaderPhase,
    request: Option<ChangeScene>,
    /// Pedido em curso (depois de aceite).
    active: Option<ChangeScene>,
    t: f32,
    settle_frames: u32,
    /// Tick em que a cena atual acabou de carregar: raízes nascidas depois são da cena.
    started_tick: Option<Tick>,
    pub last_sweep: SweepReport,
    pub last_error: Option<String>,
    /// Tempo da última troca (ms), para o profiler/QA.
    pub last_swap_ms: f32,
    /// Sem fade nem espera (editor, testes headless).
    pub instant: bool,
    /// Frames a ignorar portais depois de um carregamento: os `GlobalTransform` das
    /// entidades novas ainda não foram propagados (o portal "está" na origem).
    quiet_frames: u32,
}

/// Frames de silêncio dos portais depois de carregar uma cena.
const PORTAL_QUIET_FRAMES: u32 = 3;

impl SceneLoader {
    pub fn busy(&self) -> bool {
        self.phase != LoaderPhase::Idle || self.request.is_some()
    }
}

/// Duração de cada metade do fade (s).
pub const FADE_SECS: f32 = 0.35;
/// Tempo máximo à espera dos spawn groups / colliders antes de abrir o fade (s).
const SETTLE_TIMEOUT_FRAMES: u32 = 300;
const SETTLE_MIN_FRAMES: u32 = 4;

/// Overlay preto da troca.
#[derive(Component)]
struct SceneFadeOverlay;

/// Portais que só armam o `auto-radius` depois de o jogador sair de perto (chegar em cima
/// de um portal não o pode atravessar outra vez).
#[derive(Resource, Debug, Default)]
pub struct PortalGrace(HashSet<Entity>);

/// Raízes existentes antes do `terrain::runtime::bootstrap` (ver [`mark_bootstrap_roots`]).
#[derive(Resource, Debug, Default)]
pub struct RootSnapshot(HashSet<Entity>);

/// Antes do bootstrap do terreno: fotografa as raízes.
pub fn snapshot_roots(world: &mut World) {
    let roots = world
        .query_filtered::<Entity, Without<ChildOf>>()
        .iter(world)
        .collect();
    world.insert_resource(RootSnapshot(roots));
}

/// Depois do bootstrap: tudo o que ele pôs na raiz (água, estradas, marcadores de FX da
/// espuma/névoa…) é da cena — sem isto cada regresso à cena duplicava-os.
pub fn mark_bootstrap_roots(world: &mut World) {
    let Some(mut snapshot) = world.get_resource_mut::<RootSnapshot>() else {
        return;
    };
    let before = std::mem::take(&mut snapshot.0);
    if before.is_empty() {
        return;
    }
    let fresh: Vec<Entity> = world
        .query_filtered::<Entity, (Without<ChildOf>, Without<Persistent>)>()
        .iter(world)
        .filter(|entity| !before.contains(entity))
        .collect();
    for entity in fresh {
        world.entity_mut(entity).insert(SceneEntity);
    }
}

/// Run condition: a cena carregada já assentou (sem troca a meio de Swap/Settle). Sem o
/// carregador (apps mínimas) é sempre verdade.
pub fn scene_settled(loader: Option<Res<SceneLoader>>) -> bool {
    loader.is_none_or(|loader| !matches!(loader.phase, LoaderPhase::Swap | LoaderPhase::Settle))
}

/// Valida e enfileira uma troca. Erro se o mundo não é de um projeto, se a cena não existe
/// ou se já há uma troca em curso.
pub fn request_change(world: &mut World, request: ChangeScene) -> Result<()> {
    let current = world
        .get_resource::<CurrentScene>()
        .ok_or_else(|| anyhow!("sem CurrentScene (app sem mundo?)"))?
        .clone();
    if !current.switchable() {
        bail!(
            "este mundo não é uma cena de um projeto com `project:` no config.yaml — não há outras cenas"
        );
    }
    let project = Project::at(&current.root);
    if project.resolve_scene(&request.scene).is_none() {
        let known: Vec<String> = project.list_scenes().into_iter().map(|s| s.id).collect();
        bail!(
            "cena desconhecida `{}` (cenas: {})",
            request.scene,
            known.join(", ")
        );
    }
    let loader = world
        .get_resource_mut::<SceneLoader>()
        .ok_or_else(|| anyhow!("SceneLoaderPlugin não registado"))?;
    if loader.busy() {
        bail!("já há uma troca de cena em curso");
    }
    let mut request = request;
    if request.via_portal.is_some() && request.return_pose.is_none() {
        request.return_pose = hero_pose(world);
    }
    world.resource_mut::<SceneLoader>().request = Some(request);
    Ok(())
}

/// A pose atual do herói e o yaw da câmara de jogo.
fn hero_pose(world: &mut World) -> Option<ReturnPose> {
    let (position, rotation) = world
        .query_filtered::<&Transform, With<Player>>()
        .iter(world)
        .next()
        .map(|t| (t.translation, t.rotation))?;
    let camera_yaw_deg = world
        .query::<&crate::recipes::spawn::OrbitCamera>()
        .iter(world)
        .find(|cam| cam.target.is_none())
        .map(|cam| cam.yaw_deg)
        .unwrap_or(0.0);
    Some(ReturnPose {
        position,
        rotation,
        camera_yaw_deg,
    })
}

/// Mensagem publicada quando uma cena acabou de carregar (o editor escuta-a).
#[derive(Message, Debug, Clone)]
pub struct SceneLoaded {
    pub scene: Option<String>,
    pub path: PathBuf,
}

pub struct SceneLoaderPlugin;

impl Plugin for SceneLoaderPlugin {
    fn build(&self, app: &mut App) {
        app.init_schedule(SceneLoad);
        app.init_resource::<SceneLoader>()
            .init_resource::<SceneEpoch>()
            .init_resource::<PortalGrace>()
            .add_message::<SceneLoaded>()
            .init_resource::<SceneIndex>()
            .init_resource::<RootSnapshot>()
            .add_systems(Startup, (spawn_fade_overlay, index_scenes))
            .add_systems(PostStartup, mark_first_scene_loaded)
            .add_systems(Update, (scene_portal_system, release_portal_grace))
            .add_systems(Last, drive_scene_loader);
    }
}

fn index_scenes(current: Option<Res<CurrentScene>>, mut index: ResMut<SceneIndex>) {
    if let Some(current) = current {
        *index = SceneIndex::of(&current);
    }
}

fn spawn_fade_overlay(mut commands: Commands) {
    commands.spawn((
        Node {
            position_type: PositionType::Absolute,
            top: Val::Px(0.0),
            left: Val::Px(0.0),
            right: Val::Px(0.0),
            bottom: Val::Px(0.0),
            ..default()
        },
        BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.0)),
        GlobalZIndex(i32::MAX - 8),
        Visibility::Hidden,
        Pickable::IGNORE,
        Name::new("ui:scene-fade"),
        SceneFadeOverlay,
        Persistent,
    ));
}

/// `viber run --entry`: a entrada de chegada da cena de arranque.
#[derive(Resource, Debug, Clone, Default)]
pub struct StartEntry(pub Option<String>);

/// O arranque acabou: o que nascer daqui em diante (fora de um carregamento) é da cena.
/// Numa cena que não é a principal (`viber run --scene`) ou com `--entry`, o herói vai
/// para a entrada.
fn mark_first_scene_loaded(world: &mut World) {
    world.increment_change_tick();
    let tick = world.change_tick();
    if let Some(mut loader) = world.get_resource_mut::<SceneLoader>() {
        loader.started_tick = Some(tick);
        loader.quiet_frames = PORTAL_QUIET_FRAMES;
    }
    let entry = world
        .remove_resource::<StartEntry>()
        .and_then(|start| start.0);
    let not_main = world
        .get_resource::<CurrentScene>()
        .and_then(|scene| scene.id.clone())
        .is_some_and(|id| id != MAIN_SCENE);
    if (entry.is_some() || not_main) && !edit_mode(world) {
        let found = find_entry(world, entry.as_deref()).or_else(|| find_entry(world, None));
        if let Some((name, pose)) = found {
            place_player(world, pose.translation, Some(pose.rotation));
            if let Some(mut scene) = world.get_resource_mut::<CurrentScene>() {
                scene.entry = Some(name);
            }
        }
    }
    arm_portals(world);
}

fn edit_mode(world: &World) -> bool {
    world.get_resource::<crate::app::RunMode>().copied() == Some(crate::app::RunMode::Edit)
}

fn set_fade(world: &mut World, alpha: f32) {
    let mut q =
        world.query_filtered::<(&mut BackgroundColor, &mut Visibility), With<SceneFadeOverlay>>();
    for (mut color, mut vis) in q.iter_mut(world) {
        color.0 = Color::srgba(0.0, 0.0, 0.0, alpha.clamp(0.0, 1.0));
        *vis = if alpha > 0.001 {
            Visibility::Visible
        } else {
            Visibility::Hidden
        };
    }
}

/// A máquina (exclusiva, em `Last`): fade → troca → espera → fade.
pub fn drive_scene_loader(world: &mut World) {
    let dt = world
        .get_resource::<Time<Real>>()
        .map(|t| t.delta_secs())
        .unwrap_or(1.0 / 60.0)
        .min(0.1);
    let edit = edit_mode(world) || world.resource::<SceneLoader>().instant;
    let phase = world.resource::<SceneLoader>().phase;
    match phase {
        LoaderPhase::Idle => {
            let mut loader = world.resource_mut::<SceneLoader>();
            loader.quiet_frames = loader.quiet_frames.saturating_sub(1);
            let Some(request) = loader.request.take() else {
                return;
            };
            loader.active = Some(request);
            loader.t = 0.0;
            loader.phase = if edit {
                LoaderPhase::Swap
            } else {
                LoaderPhase::FadeOut
            };
        }
        LoaderPhase::FadeOut => {
            let t = {
                let mut loader = world.resource_mut::<SceneLoader>();
                loader.t += dt / FADE_SECS;
                loader.t
            };
            set_fade(world, t);
            if t >= 1.0 {
                world.resource_mut::<SceneLoader>().phase = LoaderPhase::Swap;
            }
        }
        LoaderPhase::Swap => {
            let request = world
                .resource_mut::<SceneLoader>()
                .active
                .clone()
                .expect("Swap sem pedido");
            let started = std::time::Instant::now();
            let result = swap_scene(world, &request);
            let mut loader = world.resource_mut::<SceneLoader>();
            loader.last_swap_ms = started.elapsed().as_secs_f32() * 1000.0;
            loader.settle_frames = 0;
            match result {
                Ok(()) => {
                    loader.last_error = None;
                    loader.phase = if edit {
                        LoaderPhase::Idle
                    } else {
                        LoaderPhase::Settle
                    };
                }
                Err(error) => {
                    warn!("troca de cena para `{}` falhou: {error:#}", request.scene);
                    loader.last_error = Some(format!("{error:#}"));
                    // A cena antiga continua (a validação corre antes do varrimento).
                    loader.phase = if edit {
                        LoaderPhase::Idle
                    } else {
                        LoaderPhase::FadeIn
                    };
                    loader.t = 1.0;
                }
            }
            if edit {
                loader.active = None;
            }
        }
        LoaderPhase::Settle => {
            let frames = {
                let mut loader = world.resource_mut::<SceneLoader>();
                loader.settle_frames += 1;
                loader.settle_frames
            };
            let groups_pending = world
                .get_resource::<crate::spawner::PendingSpawnGroups>()
                .is_some_and(|groups| groups.groups.iter().any(|g| !g.done));
            let settling = world
                .query_filtered::<(), (With<Player>, With<crate::player::TeleportSettle>)>()
                .iter(world)
                .next()
                .is_some();
            let ready = frames >= SETTLE_MIN_FRAMES && !groups_pending && !settling;
            if ready || frames >= SETTLE_TIMEOUT_FRAMES {
                let mut loader = world.resource_mut::<SceneLoader>();
                loader.phase = LoaderPhase::FadeIn;
                loader.t = 1.0;
            }
        }
        LoaderPhase::FadeIn => {
            let t = {
                let mut loader = world.resource_mut::<SceneLoader>();
                loader.t -= dt / FADE_SECS;
                loader.t
            };
            set_fade(world, t);
            if t <= 0.0 {
                let mut loader = world.resource_mut::<SceneLoader>();
                loader.phase = LoaderPhase::Idle;
                loader.active = None;
            }
        }
    }
}

/// Valida a cena alvo, descarrega a atual, carrega a nova e põe o jogador na entrada.
pub fn swap_scene(world: &mut World, request: &ChangeScene) -> Result<()> {
    let current = world.resource::<CurrentScene>().clone();
    let project = Project::at(&current.root);
    let path = project
        .resolve_scene(&request.scene)
        .ok_or_else(|| anyhow!("cena desconhecida `{}`", request.scene))?;
    let target_id = project
        .scene_id(&path)
        .unwrap_or_else(|| request.scene.clone());
    // Parse ANTES do varrimento: uma cena partida deixa a atual intacta.
    let loaded = crate::xml::include::load_world_in(&path, &project.root)
        .map_err(|error| anyhow!("a carregar {}: {error:#}", path.display()))?;
    let parsed = crate::recipes::parse_world(&loaded.root_attrs, &loaded.nodes)
        .map_err(|error| anyhow!("a parsear {}: {error:#}", path.display()))?;
    if !parsed.static_world {
        bail!(
            "{}: cena procedural (sem static=\"true\") — converte-a com `viber bake`",
            path.display()
        );
    }
    let title = loaded
        .root_attrs
        .iter()
        .find(|(k, _)| k == "title")
        .map(|(_, v)| v.clone());
    // Uma cinemática da cena antiga (planos em coordenadas dela) não continua na nova.
    if world
        .get_resource::<crate::cinematic::CinematicPlayback>()
        .is_some_and(|playback| playback.playing().is_some())
    {
        world.write_message(crate::cinematic::StopCinematic);
    }
    if let Some(mut queue) = world.get_resource_mut::<crate::luau::events::ScriptEventQueue>() {
        queue.push(crate::luau::events::ScriptGameEvent::SceneUnloading {
            scene: current.id.clone().unwrap_or_default(),
        });
    }

    // `@back`: o portal por onde se entrou na cena que se deixa (se o alvo for essa cena).
    let entry_name = match &request.arrival {
        Arrival::Entry(name) => name.clone(),
        Arrival::Back => current
            .came_from
            .as_ref()
            .filter(|(scene, _)| *scene == target_id)
            .map(|(_, portal)| portal.clone()),
        Arrival::Position(_) | Arrival::Keep => None,
    };
    let came_from = request.came_from.clone().or_else(|| {
        request
            .via_portal
            .as_ref()
            .and_then(|portal| current.id.clone().map(|scene| (scene, portal.clone())))
    });

    // Biblioteca de cinemáticas: união (as do autoload/principal continuam tocáveis).
    if let Some(mut library) = world.get_resource_mut::<crate::cinematic::CinematicLibrary>() {
        for spec in &parsed.cinematics {
            library.0.insert(spec.id.clone(), spec.clone());
        }
    }

    let before = world.entities().count_spawned();
    let mut report = unload_scene(world);
    report.entities_before = before as usize;

    world.insert_resource(crate::recipes::spawn::PendingWorld {
        world: parsed,
        base_dir: Some(project.root.clone()),
    });
    match crate::scene::doc::SceneDoc::load(&path) {
        Ok(doc) => world.insert_resource(doc),
        Err(error) => warn!("documento de edição de {}: {error:#}", path.display()),
    }
    world.insert_resource(crate::scene::ops::History::default());
    // A época sobe ANTES do carregamento: o `spawn::startup` lê-a para saber que isto é
    // uma troca (não repõe o relógio do dia nem cria a câmara de recurso).
    world.resource_mut::<SceneEpoch>().0 += 1;
    world.run_schedule(SceneLoad);
    world.flush();
    world.increment_change_tick();
    let tick = world.change_tick();
    report.entities_after = world.entities().count_spawned() as usize;
    {
        let mut loader = world.resource_mut::<SceneLoader>();
        loader.started_tick = Some(tick);
        loader.last_sweep = report;
        loader.quiet_frames = PORTAL_QUIET_FRAMES;
    }

    let arrived_at = match &request.arrival {
        Arrival::Keep => None,
        Arrival::Position(pos) => {
            place_player(world, *pos, None);
            None
        }
        Arrival::Back
            if current
                .came_from
                .as_ref()
                .is_some_and(|(scene, _)| *scene == target_id)
                && current.return_pose.is_some() =>
        {
            // Volta exatamente por onde saiu: de costas para a porta, câmara de fora.
            let pose = current.return_pose.expect("verificado");
            let turned = pose.rotation * Quat::from_rotation_y(std::f32::consts::PI);
            place_player(world, pose.position, Some(turned));
            set_camera_yaw(world, pose.camera_yaw_deg);
            entry_name.clone()
        }
        Arrival::Entry(_) | Arrival::Back => {
            let entry = find_entry(world, entry_name.as_deref());
            if entry.is_none() && entry_name.is_some() {
                warn!(
                    "cena `{target_id}`: entrada `{}` não existe — a usar a primeira",
                    entry_name.as_deref().unwrap_or_default()
                );
            }
            let entry = entry.or_else(|| find_entry(world, None));
            if let Some((name, pose)) = &entry {
                place_player(world, pose.translation, Some(pose.rotation));
                Some(name.clone())
            } else {
                None
            }
        }
    };
    arm_portals(world);

    *world.resource_mut::<CurrentScene>() = CurrentScene {
        root: current.root.clone(),
        id: Some(target_id.clone()),
        path: path.clone(),
        title,
        entry: arrived_at.clone(),
        came_from,
        return_pose: request.return_pose,
    };
    if let Some(toast) = &request.toast {
        world.write_message(crate::luau::ScriptToast(toast.clone()));
    }
    if let Some(clip) = request
        .sound
        .as_deref()
        .and_then(crate::luau::sfx::sfx_clip_from_str)
    {
        world.write_message(crate::ambient::SfxEvent {
            clip,
            position: None,
        });
    }
    if let Some(mut queue) = world.get_resource_mut::<crate::luau::events::ScriptEventQueue>() {
        queue.push(crate::luau::events::ScriptGameEvent::SceneLoaded {
            scene: target_id.clone(),
            entry: arrived_at.unwrap_or_default(),
        });
    }
    let index = SceneIndex::of(world.resource::<CurrentScene>());
    world.insert_resource(index);
    world.write_message(SceneLoaded {
        scene: Some(target_id),
        path,
    });
    Ok(())
}

/// Despawn de todas as raízes da cena + reset dos recursos por cena.
pub fn unload_scene(world: &mut World) -> SweepReport {
    let started = world.resource::<SceneLoader>().started_tick;
    let mut query = world.query_filtered::<
        (Entity, Has<SceneEntity>, SpawnDetails, Option<&Name>),
        (With<Transform>, Without<ChildOf>, Without<Persistent>),
    >();
    let this_run = world.change_tick();
    let doomed: Vec<(Entity, Option<String>)> = query
        .iter(world)
        .filter(|(_, tagged, details, _)| {
            *tagged
                || started.is_some_and(|tick| details.spawn_tick().is_newer_than(tick, this_run))
        })
        .map(|(entity, _, _, name)| (entity, name.map(|n| n.as_str().to_owned())))
        .collect();
    let mut counts: std::collections::HashMap<String, usize> = Default::default();
    for (_, name) in &doomed {
        let key = name
            .as_deref()
            .unwrap_or("?")
            .split([' ', '.', ':'])
            .next()
            .unwrap_or("?")
            .to_owned();
        *counts.entry(key).or_default() += 1;
    }
    let mut top: Vec<(String, usize)> = counts.into_iter().collect();
    top.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    top.truncate(12);
    let roots = doomed.len();
    for (entity, _) in doomed {
        if let Ok(entity) = world.get_entity_mut(entity) {
            entity.despawn();
        }
    }
    world.flush();
    reset_scene_resources(world);
    SweepReport {
        roots,
        top,
        ..SweepReport::default()
    }
}

/// Recursos de UMA cena: removidos (o carregamento seguinte volta a inseri-los) ou repostos.
pub fn reset_scene_resources(world: &mut World) {
    use crate::recipes::spawn::{EditorGroups, PendingTerrain, PrototypeLibrary, StaticWorld};
    world.remove_resource::<crate::terrain::runtime::TerrainChunkMaterials>();
    world.remove_resource::<crate::terrain::runtime::TerrainSplat>();
    world.remove_resource::<crate::terrain::runtime::TerrainRuntime>();
    world.remove_resource::<PendingTerrain>();
    world.remove_resource::<crate::recipes::spawn::PendingWorld>();
    world.remove_resource::<crate::worldsys::WorldBorderConfig>();
    world.remove_resource::<crate::worldsys::InteriorSceneConfig>();
    world.remove_resource::<crate::worldsys::BiomeRegions>();
    world.remove_resource::<crate::worldsys::EngineConfigs>();
    world.remove_resource::<PrototypeLibrary>();
    world.remove_resource::<StaticWorld>();
    world.remove_resource::<EditorGroups>();
    world.remove_resource::<crate::spawner::SpawnExclusions>();
    world.remove_resource::<crate::spawner::PendingSpawnGroups>();
    world.insert_resource(GlobalAmbientLight::default());
    world.insert_resource(ClearColor::default());
    reset_default::<crate::terrain::plugin::ChunkLodState>(world);
    reset_default::<crate::physics::ColliderCache>(world);
    reset_default::<crate::physics::TerrainCollisionStatus>(world);
    reset_default::<crate::interact::InteractionFocus>(world);
    reset_default::<crate::worldsys::InteriorLighting>(world);
    reset_default::<crate::grass::GrassField>(world);
    reset_default::<crate::terrain::delta::TerrainEditQueue>(world);
    reset_default::<PortalGrace>(world);
    crate::nav::reset_for_scene(world);
}

fn reset_default<R: Resource + Default>(world: &mut World) {
    if world.contains_resource::<R>() {
        world.insert_resource(R::default());
    }
}

/// Pose mundo de uma entidade recém-spawnada (sem esperar pela propagação).
fn world_pose(world: &World, entity: Entity) -> Transform {
    let mut pose = world.get::<Transform>(entity).copied().unwrap_or_default();
    let mut current = entity;
    while let Some(parent) = world.get::<ChildOf>(current).map(ChildOf::parent) {
        if let Some(parent_tf) = world.get::<Transform>(parent) {
            pose = parent_tf.mul_transform(pose);
        }
        current = parent;
    }
    pose
}

/// Entrada por nome (ou a primeira: `<SceneEntry>` antes de portais). Devolve o nome e a
/// pose mundo da chegada (com o `arrive-offset` aplicado).
pub fn find_entry(world: &mut World, name: Option<&str>) -> Option<(String, Transform)> {
    let mut query = world.query::<(Entity, &SceneEntryPoint, Has<ScenePortal>)>();
    let mut candidates: Vec<(Entity, String, Vec3, bool)> = query
        .iter(world)
        .map(|(e, p, portal)| (e, p.name.clone(), p.offset, portal))
        .collect();
    // Ordem estável: entradas puras antes de portais, depois por id.
    candidates.sort_by_key(|(e, _, _, portal)| (*portal, e.index()));
    let (entity, found, offset, _) = match name {
        Some(name) => candidates.into_iter().find(|(_, n, _, _)| n == name)?,
        None => candidates.into_iter().next()?,
    };
    let mut pose = world_pose(world, entity);
    pose.translation += pose.rotation * offset;
    Some((found, pose))
}

/// Põe o herói em `pos` (assentado no terreno, se houver) virado para `rotation`.
pub fn place_player(world: &mut World, pos: Vec3, rotation: Option<Quat>) {
    let landed = crate::player::landing_position(
        world.get_resource::<crate::terrain::runtime::TerrainRuntime>(),
        pos,
    );
    let mut heroes = world.query_filtered::<(Entity, &mut Transform, &mut Player), With<Player>>();
    let Some((entity, mut transform, mut player)) = heroes.iter_mut(world).next() else {
        return;
    };
    transform.translation = landed;
    if let Some(rotation) = rotation {
        let forward = rotation * Vec3::Z;
        let yaw = forward.x.atan2(forward.z);
        transform.rotation = Quat::from_rotation_y(yaw);
    }
    player.vel_x = 0.0;
    player.vel_y = 0.0;
    player.vel_z = 0.0;
    let facing = transform.rotation * Vec3::Z;
    world
        .entity_mut(entity)
        .remove::<crate::physics_fx::Knockback>()
        .insert(crate::player::TeleportSettle::default());
    // A câmara de jogo salta para trás do herói (sem varrer o mapa a partir da cena antiga).
    let mut cams = world.query::<&mut crate::recipes::spawn::OrbitCamera>();
    for mut cam in cams.iter_mut(world) {
        if cam.target.is_none() {
            cam.yaw_deg = crate::camera::behind_yaw_deg(facing.x, facing.z);
            cam.initialized = false;
        }
    }
}

/// Fixa o yaw da câmara de jogo (sem varrer: `initialized = false` encaixa no frame seguinte).
fn set_camera_yaw(world: &mut World, yaw_deg: f32) {
    let mut cams = world.query::<&mut crate::recipes::spawn::OrbitCamera>();
    for mut cam in cams.iter_mut(world) {
        if cam.target.is_none() {
            cam.yaw_deg = yaw_deg;
            cam.initialized = false;
        }
    }
}

/// Portais com o herói já dentro do `auto-radius` ficam desarmados até ele sair.
fn arm_portals(world: &mut World) {
    let hero = world
        .query_filtered::<&Transform, With<Player>>()
        .iter(world)
        .next()
        .map(|t| t.translation);
    let Some(hero) = hero else {
        return;
    };
    let portals: Vec<Entity> = {
        let mut q = world.query::<(Entity, &ScenePortal)>();
        q.iter(world)
            .filter(|(_, portal)| portal.0.auto_radius.is_some())
            .map(|(e, _)| e)
            .collect()
    };
    let mut grace = HashSet::new();
    for entity in portals {
        let pos = world_pose(world, entity).translation;
        let radius = world
            .get::<ScenePortal>(entity)
            .and_then(|p| p.0.auto_radius)
            .unwrap_or(0.0);
        if pos.xz().distance(hero.xz()) <= radius + 0.75 {
            grace.insert(entity);
        }
    }
    world.insert_resource(PortalGrace(grace));
}

fn release_portal_grace(
    mut grace: ResMut<PortalGrace>,
    loader: Res<SceneLoader>,
    portals: Query<(&GlobalTransform, &ScenePortal)>,
    heroes: Query<&GlobalTransform, With<Player>>,
) {
    if grace.0.is_empty() || loader.busy() || loader.quiet_frames > 0 {
        return;
    }
    let Some(hero) = heroes.iter().next().map(GlobalTransform::translation) else {
        return;
    };
    grace.0.retain(|entity| {
        portals.get(*entity).is_ok_and(|(tf, portal)| {
            tf.translation().xz().distance(hero.xz()) <= portal.0.auto_radius.unwrap_or(0.0) + 0.75
        })
    });
}

/// [E] perto do portal (vencedor do foco) ou entrar no `auto-radius` → troca de cena.
#[allow(clippy::too_many_arguments)]
fn scene_portal_system(
    keys: Option<Res<ButtonInput<KeyCode>>>,
    focus: Option<Res<crate::interact::InteractionFocus>>,
    grace: Res<PortalGrace>,
    loader: Res<SceneLoader>,
    current: Option<Res<CurrentScene>>,
    mode: Option<Res<crate::app::RunMode>>,
    menus: Option<Res<crate::menus::MenusOpen>>,
    portals: Query<(Entity, &GlobalTransform, &ScenePortal, Option<&Name>)>,
    heroes: Query<&GlobalTransform, With<Player>>,
    mut commands: Commands,
) {
    if mode.as_deref() == Some(&crate::app::RunMode::Edit)
        || loader.busy()
        || loader.quiet_frames > 0
        // Menus, loja, viagem ou cinemática roubam o input (como ao herói).
        || menus.as_deref().is_some_and(crate::menus::MenusOpen::any)
    {
        return;
    }
    if !current.as_deref().is_some_and(CurrentScene::switchable) {
        return;
    }
    let Some(hero) = heroes.iter().next().map(GlobalTransform::translation) else {
        return;
    };
    for (entity, tf, portal, name) in &portals {
        let spec = &portal.0;
        let by_key = crate::luau::input::key_code_from_str(&spec.key).is_some_and(|key| {
            keys.as_deref().is_some_and(|k| k.just_pressed(key))
                && focus.as_deref().is_some_and(|f| f.wins(key, entity))
        });
        let by_walk = spec.auto_radius.is_some_and(|r| {
            !grace.0.contains(&entity) && tf.translation().xz().distance(hero.xz()) <= r
        });
        if !(by_key || by_walk) {
            continue;
        }
        let request = ChangeScene {
            scene: spec.target.clone(),
            arrival: Arrival::from_entry(spec.entry.as_deref()),
            via_portal: name.map(|n| n.as_str().to_owned()),
            toast: spec.toast.clone(),
            sound: spec.sound.clone(),
            came_from: None,
            return_pose: None,
        };
        commands.queue(move |world: &mut World| {
            if let Err(error) = request_change(world, request) {
                warn!("portal: {error:#}");
            }
        });
        return;
    }
}

/// Resolve o caminho de arranque de `viber run`: uma pasta de projeto vira a cena
/// principal; `--scene <id>` escolhe outra cena do projeto de `path`.
pub fn resolve_run_path(path: &std::path::Path, scene: Option<&str>) -> Result<PathBuf> {
    let project = Project::discover(path);
    let base = if path.is_dir() {
        project.main_scene_path()
    } else {
        path.to_path_buf()
    };
    let Some(scene) = scene else {
        return Ok(base);
    };
    project.resolve_scene(scene).ok_or_else(|| {
        let known: Vec<String> = project.list_scenes().into_iter().map(|s| s.id).collect();
        anyhow!(
            "--scene `{scene}`: cena desconhecida no projeto {} (cenas: {})",
            project.root.display(),
            known.join(", ")
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const CONFIG: &str = "assets:\n  roots: []\n  bgm_dir: bgm\n  sfx_dir: sfx\n  terrain_textures_dir: t\n\
                          game:\n  scripts_dir: scripts\nsave:\n  dir: /tmp\n\
                          project:\n  autoload: [world/autoload.xml]\n";

    /// Projeto de duas cenas sem terreno: a vila (com a porta da casa) e a casa.
    fn project() -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        std::fs::write(root.join("config.yaml"), CONFIG).unwrap();
        std::fs::create_dir_all(root.join("world")).unwrap();
        std::fs::create_dir_all(root.join("scenes")).unwrap();
        std::fs::write(
            root.join("world/autoload.xml"),
            r#"<world><PlayerGLTF name="hero" model-url="/hero.glb" translation="0 1 0"/></world>"#,
        )
        .unwrap();
        std::fs::write(
            root.join("world.xml"),
            r#"<world static="true" title="Vila">
  <Cuboid name="town-well" translation="1 0 1" half-size="1 1 1"/>
  <ScenePortal name="portal.house" translation="5 0 0" target="house" entry="from-town"/>
  <ScenePortal name="portal.house.back" translation="-5 0 0" target="house" entry="from-town"/>
</world>"#,
        )
        .unwrap();
        std::fs::write(
            root.join("scenes/house.xml"),
            r#"<world static="true" title="Casa">
  <Cuboid name="table" translation="0 0 0" half-size="1 0.5 1"/>
  <SceneEntry name="from-town" translation="0 0.6 3"/>
  <ScenePortal name="exit" translation="0 0 -4" target="main" entry="@back" auto-radius="1"/>
</world>"#,
        )
        .unwrap();
        dir
    }

    fn boot(root: &std::path::Path) -> App {
        let world_path = root.join("world.xml");
        let config = crate::config::load(root).unwrap();
        let loaded = crate::xml::include::load_world(&world_path).unwrap();
        let parsed = crate::recipes::parse_world(&loaded.root_attrs, &loaded.nodes).unwrap();
        let auto =
            crate::xml::include::load_world_in(&root.join("world/autoload.xml"), root).unwrap();
        let auto = crate::recipes::parse_world(&auto.root_attrs, &auto.nodes).unwrap();
        let mut app = crate::bake::headless_app(&world_path, &config, parsed).unwrap();
        app.add_plugins(crate::scene::live::ScenePlugin);
        app.insert_resource(crate::scene::doc::SceneDoc::load(&world_path).unwrap());
        app.insert_resource(CurrentScene::for_path(
            &world_path.canonicalize().unwrap(),
            None,
        ));
        app.insert_resource(PendingAutoload(vec![auto]));
        app.add_systems(Startup, crate::recipes::spawn::startup);
        app.add_systems(SceneLoad, crate::recipes::spawn::startup);
        app.add_plugins(SceneLoaderPlugin);
        app.world_mut().resource_mut::<SceneLoader>().instant = true;
        app.update();
        app
    }

    fn named(app: &mut App, name: &str) -> Vec<Entity> {
        let mut q = app.world_mut().query::<(Entity, &Name)>();
        q.iter(app.world())
            .filter(|(_, n)| n.as_str() == name)
            .map(|(e, _)| e)
            .collect()
    }

    fn hero_pos(app: &mut App) -> Vec3 {
        let mut q = app.world_mut().query_filtered::<&Transform, With<Player>>();
        q.iter(app.world()).next().expect("herói").translation
    }

    fn change(app: &mut App, request: ChangeScene) {
        request_change(app.world_mut(), request).unwrap();
        for _ in 0..4 {
            app.update();
        }
        assert_eq!(
            app.world().resource::<SceneLoader>().phase,
            LoaderPhase::Idle
        );
        assert!(app.world().resource::<SceneLoader>().last_error.is_none());
    }

    #[test]
    fn portal_round_trip_swaps_universes_and_keeps_the_hero() {
        let tmp = project();
        let mut app = boot(tmp.path());
        assert_eq!(
            app.world().resource::<CurrentScene>().id.as_deref(),
            Some("main")
        );
        assert_eq!(named(&mut app, "hero").len(), 1);
        assert_eq!(named(&mut app, "town-well").len(), 1);
        let hero = named(&mut app, "hero")[0];
        // Medido depois da 1.ª ida e volta: a cena insere recursos (que na Bevy 0.20 também
        // são entidades) que a principal não declarava.
        let mut baseline = None;

        for round in 0..3 {
            // O herói está à porta (fora) quando a usa.
            place_player(app.world_mut(), Vec3::new(-5.0, 0.0, 1.2), None);
            let mut enter = ChangeScene::new("house", Arrival::Entry(Some("from-town".into())));
            enter.via_portal = Some("portal.house.back".into());
            change(&mut app, enter);
            let scene = app.world().resource::<CurrentScene>().clone();
            assert_eq!(scene.id.as_deref(), Some("house"), "ronda {round}");
            assert_eq!(
                scene.came_from,
                Some(("main".into(), "portal.house.back".into()))
            );
            assert!(
                named(&mut app, "town-well").is_empty(),
                "a vila foi descarregada"
            );
            assert_eq!(named(&mut app, "table").len(), 1);
            assert_eq!(
                named(&mut app, "hero"),
                [hero],
                "o herói persiste (mesma entidade)"
            );
            assert!((hero_pos(&mut app) - Vec3::new(0.0, 0.6, 3.0)).length() < 1e-3);

            change(&mut app, ChangeScene::new("main", Arrival::Back));
            assert_eq!(
                app.world().resource::<CurrentScene>().id.as_deref(),
                Some("main")
            );
            assert!(named(&mut app, "table").is_empty());
            assert_eq!(named(&mut app, "town-well").len(), 1);
            assert_eq!(named(&mut app, "portal.house").len(), 1);
            // `@back` volta à porta por onde se entrou (a segunda, não a primeira), ao sítio
            // exato onde o herói a usou.
            assert!((hero_pos(&mut app) - Vec3::new(-5.0, 0.0, 1.2)).length() < 1e-3);
            let count = app.world().entities().count_spawned();
            assert_eq!(
                *baseline.get_or_insert(count),
                count,
                "ronda {round}: nada fica para trás nem se duplica"
            );
        }
        assert_eq!(app.world().resource::<SceneEpoch>().0, 6);
    }

    #[test]
    fn back_without_a_return_pose_lands_on_the_portal() {
        let tmp = project();
        let mut app = boot(tmp.path());
        let mut enter = ChangeScene::new("house", Arrival::Entry(None));
        enter.via_portal = Some("portal.house.back".into());
        change(&mut app, enter);
        // Um save (ou um agente) sem pose: cai na entrada com o nome do portal.
        app.world_mut().resource_mut::<CurrentScene>().return_pose = None;
        change(&mut app, ChangeScene::new("main", Arrival::Back));
        assert!((hero_pos(&mut app) - Vec3::new(-5.0, 0.0, 0.0)).length() < 1e-3);
    }

    #[test]
    fn unknown_scene_is_refused_and_nothing_unloads() {
        let tmp = project();
        let mut app = boot(tmp.path());
        let err = request_change(app.world_mut(), ChangeScene::new("nope", Arrival::Keep));
        assert!(err.unwrap_err().to_string().contains("nope"));
        app.update();
        assert_eq!(named(&mut app, "town-well").len(), 1);
    }

    #[test]
    fn worlds_outside_a_declared_project_cannot_switch() {
        let tmp = project();
        std::fs::write(
            tmp.path().join("config.yaml"),
            CONFIG.split("project:").next().unwrap(),
        )
        .unwrap();
        let current = CurrentScene::for_path(&tmp.path().join("world.xml"), None);
        assert!(!current.switchable());
    }

    #[test]
    fn run_path_resolves_project_dirs_and_scene_ids() {
        let tmp = project();
        let main = resolve_run_path(tmp.path(), None).unwrap();
        assert!(main.ends_with("world.xml"));
        let house = resolve_run_path(&tmp.path().join("world.xml"), Some("house")).unwrap();
        assert!(house.ends_with("scenes/house.xml"));
        assert!(resolve_run_path(tmp.path(), Some("nope")).is_err());
    }
}
