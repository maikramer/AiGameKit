//! Instrumentação de sistemas — o equivalente nativo do `recordSystemTiming`
//! do profiler do VibeGame. O Bevy 0.19 não expõe tempos por sistema (removidos
//! a montante), por isso [`timed`] embrulha qualquer `System` e mede o
//! `run_unsafe` dele: mesma assinatura, mesmo acesso, +2 leituras de relógio.
//!
//! Os registos vivem em globais porque sistemas correm em paralelo e o wrapper
//! não tem acesso ao `World`; é um `Mutex` por *frame* por sistema (~20 ns).
//! O congelamento ([`set_frozen`]) pára a aquisição mas preserva o anel —
//! igual ao *Pause* do VibeGame. Escopos dinâmicos (por script Luau) usam
//! [`record_script`]; sistemas estáticos usam [`record_system`].

use std::collections::BTreeMap;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Instant;

use serde::Serialize;

use bevy::ecs::system::{IntoSystem, System, SystemIn, SystemStateFlags};
use bevy::prelude::*;

/// Janela deslizante por sistema — ~2 s a 60 fps (o VibeGame usa 120).
pub const RING_SIZE: usize = 120;

/// Amostras ≥ este valor marcam a linha como quente (`!` na tabela), como o
/// `HOT_MS = 1.0` do VibeGame.
pub const HOT_MS: f32 = 1.0;

/// Grupos do profiler — o agrupamento do VibeGame é por fase de scheduler;
/// aqui é por subsistema (mais útil numa engine where owners são módulos).
/// `physics` mede o `PhysicsSet::StepSimulation` inteiro via âncoras (ver
/// [`physics_anchors`]), não um sistema embrulhado.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Group {
    Ai,
    Camera,
    Combat,
    /// Fases do PRÓPRIO Bevy medidas por âncoras (`engine_phase_anchors`):
    /// propagação de transforms, visibilidade, visibilidade por luz. Não são
    /// código nosso, mas dominam o frame num mundo de 58 k entidades e sem
    /// elas o profiler mostra 4 ms de um frame de 48.
    Engine,
    Fx,
    Hud,
    Player,
    Physics,
    Render,
    Scripts,
    Spawner,
    Terrain,
    World,
    Other,
}

impl Group {
    pub fn as_str(self) -> &'static str {
        match self {
            Group::Ai => "ai",
            Group::Camera => "camera",
            Group::Combat => "combat",
            Group::Engine => "engine",
            Group::Fx => "fx",
            Group::Hud => "hud",
            Group::Player => "player",
            Group::Physics => "physics",
            Group::Render => "render",
            Group::Scripts => "scripts",
            Group::Spawner => "spawner",
            Group::Terrain => "terrain",
            Group::World => "world",
            Group::Other => "other",
        }
    }

    pub const ALL: [Group; 14] = [
        Group::Ai,
        Group::Camera,
        Group::Combat,
        Group::Engine,
        Group::Fx,
        Group::Hud,
        Group::Player,
        Group::Physics,
        Group::Render,
        Group::Scripts,
        Group::Spawner,
        Group::Terrain,
        Group::World,
        Group::Other,
    ];
}

/// Estatísticas de um sistema/escopo — mesmo shape do `ProfilerTimingStats`
/// do VibeGame (avg/min/max/p95/last/pct/amostras).
#[derive(Debug, Clone, Serialize)]
pub struct TimingStats {
    pub name: String,
    pub group: &'static str,
    pub avg_ms: f32,
    pub min_ms: f32,
    pub max_ms: f32,
    pub p95_ms: f32,
    pub last_ms: f32,
    /// Percentagem do frame médio (0–100).
    pub pct: f32,
    pub samples: u64,
}

/// Uma entrada do anel: amostras + último valor + total histórico.
#[derive(Default)]
struct Ring {
    samples: RingSamples,
    head: usize,
    filled: usize,
    total: u64,
    last: f32,
}

/// `[f32; 120]` sem `Default` (arrays >32 não derivam) — zeroed é válido.
struct RingSamples([f32; RING_SIZE]);

impl Default for RingSamples {
    fn default() -> Self {
        Self([0.0; RING_SIZE])
    }
}

impl core::ops::Deref for RingSamples {
    type Target = [f32; RING_SIZE];
    fn deref(&self) -> &[f32; RING_SIZE] {
        &self.0
    }
}

impl core::ops::DerefMut for RingSamples {
    fn deref_mut(&mut self) -> &mut [f32; RING_SIZE] {
        &mut self.0
    }
}

impl Ring {
    fn push(&mut self, ms: f32) {
        self.samples[self.head] = ms;
        self.head = (self.head + 1) % RING_SIZE;
        self.filled = (self.filled + 1).min(RING_SIZE);
        self.total += 1;
        self.last = ms;
    }

    /// (avg, min, max, p95) sobre a janela preenchida. p95 = percentil 95
    /// discreto, como o `ringStats` do VibeGame.
    fn stats(&self) -> (f32, f32, f32, f32) {
        if self.filled == 0 {
            return (0.0, 0.0, 0.0, 0.0);
        }
        let mut sum = 0.0f32;
        let mut min = f32::INFINITY;
        let mut max = 0.0f32;
        for &v in &self.samples[..self.filled] {
            sum += v;
            min = min.min(v);
            max = max.max(v);
        }
        let mut sorted = self.samples[..self.filled].to_vec();
        sorted.sort_by(|a, b| a.total_cmp(b));
        // p95 discreto do VibeGame: ceil(n·0.95)-1.
        let p95_idx = (self.filled * 95).div_ceil(100) - 1;
        (sum / self.filled as f32, min, max, sorted[p95_idx])
    }
}

/// `&'static str` para sistemas (zero alloc por registo); `String` para
/// scripts (alloc só no 1.º sighting de cada caminho).
static SYSTEM_RINGS: Mutex<BTreeMap<&'static str, (Group, Ring)>> = Mutex::new(BTreeMap::new());
static SCRIPT_RINGS: Mutex<BTreeMap<String, Ring>> = Mutex::new(BTreeMap::new());
static FROZEN: AtomicBool = AtomicBool::new(false);

/// Congela a aquisição (aneis ficam estáveis para leitura/exportação).
pub fn set_frozen(frozen: bool) {
    FROZEN.store(frozen, Ordering::Relaxed);
}

pub fn is_frozen() -> bool {
    FROZEN.load(Ordering::Relaxed)
}

/// Registra a duração de um sistema estático (chamado por [`timed`]).
pub fn record_system(group: Group, key: &'static str, ms: f32) {
    if is_frozen() || !ms.is_finite() {
        return;
    }
    if let Ok(mut map) = SYSTEM_RINGS.lock() {
        map.entry(key)
            .or_insert((group, Ring::default()))
            .1
            .push(ms);
    }
}

/// Registra um escopo dinâmico `script/<caminho>` — a secção "Entity scripts
/// (per file)" do VibeGame. Zero alloc quando o caminho já é conhecido.
pub fn record_script(path: &str, ms: f32) {
    if is_frozen() || !ms.is_finite() {
        return;
    }
    if let Ok(mut map) = SCRIPT_RINGS.lock() {
        if let Some(ring) = map.get_mut(path) {
            ring.push(ms);
            return;
        }
        let mut ring = Ring::default();
        ring.push(ms);
        map.insert(path.to_string(), ring);
    }
}

fn stats_to_timing(
    name: String,
    group: &'static str,
    ring: &Ring,
    frame_avg_ms: f32,
) -> TimingStats {
    let (avg, min, max, p95) = ring.stats();
    TimingStats {
        pct: if frame_avg_ms > 0.0 {
            avg / frame_avg_ms * 100.0
        } else {
            0.0
        },
        name,
        group,
        avg_ms: avg,
        min_ms: min,
        max_ms: max,
        p95_ms: p95,
        last_ms: ring.last,
        samples: ring.total,
    }
}

/// Snapshot dos sistemas instrumentados, ordenado por média desc.
pub fn systems_snapshot(frame_avg_ms: f32) -> Vec<TimingStats> {
    let Ok(map) = SYSTEM_RINGS.lock() else {
        return Vec::new();
    };
    let mut rows: Vec<TimingStats> = map
        .iter()
        .map(|(&name, (group, ring))| {
            stats_to_timing(name.to_string(), group.as_str(), ring, frame_avg_ms)
        })
        .collect();
    rows.sort_by(|a, b| b.avg_ms.total_cmp(&a.avg_ms));
    rows
}

/// Snapshot dos escopos por script (`script/<caminho>`), média desc.
pub fn scripts_snapshot(frame_avg_ms: f32) -> Vec<TimingStats> {
    let Ok(map) = SCRIPT_RINGS.lock() else {
        return Vec::new();
    };
    let mut rows: Vec<TimingStats> = map
        .iter()
        .map(|(name, ring)| stats_to_timing(name.clone(), "scripts", ring, frame_avg_ms))
        .collect();
    rows.sort_by(|a, b| b.avg_ms.total_cmp(&a.avg_ms));
    rows
}

/// Totais por grupo na janela — as "Groups bars" do VibeGame.
pub fn groups_snapshot(frame_avg_ms: f32) -> Vec<(Group, TimingStats)> {
    let Ok(map) = SYSTEM_RINGS.lock() else {
        return Vec::new();
    };
    let mut totals: BTreeMap<Group, Ring> = BTreeMap::new();
    for (name, (group, ring)) in map.iter() {
        // A âncora do step de física já cobre os sistemas internos do Rapier;
        // somar sistemas embrulhados no mesmo grupo duplicaria tempo.
        let _ = name;
        let total = totals.entry(*group).or_default();
        // Alinha por recência: o sample mais recente vive em (head-1)%RING,
        // não na posição absoluta — anéis com heads diferentes têm de somar
        // frame-a-frame, senão a média do grupo mistura janelas.
        for i in 0..ring.filled {
            let v = ring.samples[(ring.head + RING_SIZE - 1 - i) % RING_SIZE];
            total.samples[i] += v;
        }
        total.filled = total.filled.max(ring.filled);
        total.total += ring.total;
        total.last += ring.last;
    }
    Group::ALL
        .into_iter()
        .filter_map(|group| {
            let ring = totals.get(&group)?;
            Some((
                group,
                stats_to_timing(
                    group.as_str().to_string(),
                    group.as_str(),
                    ring,
                    frame_avg_ms,
                ),
            ))
        })
        .collect()
}

/// Limpa anéis/contadores (mantém congelação) — o botão *Reset* do VibeGame.
pub fn reset_timings() {
    if let Ok(mut map) = SYSTEM_RINGS.lock() {
        map.clear();
    }
    if let Ok(mut map) = SCRIPT_RINGS.lock() {
        map.clear();
    }
}

/// Embrulha um sistema com medição de duração. Grupos vêm do primeiro
/// argumento (ex. `timed(Group::Combat, player_melee_attack)`).
///
/// Aceita qualquer `IntoSystem` (fn items de sistemas incluídos) e devolve
/// um `Timed<S>` que implementa `System` — usável em tuples, `.chain()`,
/// `.run_if()` e `.after()/.before()` como o sistema original.
///
/// ```ignore
/// app.add_systems(Update, timed(Group::Hud, hud_health_sync));
/// ```
pub fn timed<I: bevy::ecs::system::SystemInput, O, M, S: System<In = I, Out = O>>(
    group: Group,
    inner: impl IntoSystem<I, O, M, System = S>,
) -> Timed<S> {
    // O nome captura-se do ARGUMENTO (o fn item: `viber::combat::melee`) e
    // não do sistema convertido — o `type_name` do `FunctionSystem` traz o
    // caminho dos PARÂMETROS genéricos (ruído `bevy_ecs::system::…<FN(…)>`).
    let key: &'static str = std::any::type_name_of_val(&inner);
    Timed {
        group,
        key,
        inner: IntoSystem::into_system(inner),
    }
}

/// [`System`] que mede o `run_unsafe` do sistema interior e registra no anel.
/// Toda a restante trait é delegação pura — o scheduler vê o sistema original
/// (nome, acesso, flags), só que cronometrado.
pub struct Timed<S: System> {
    group: Group,
    key: &'static str,
    inner: S,
}

impl<S: System> System for Timed<S> {
    type In = S::In;
    type Out = S::Out;

    fn name(&self) -> bevy::utils::DebugName {
        self.inner.name()
    }

    fn system_type(&self) -> std::any::TypeId {
        self.inner.system_type()
    }

    fn flags(&self) -> SystemStateFlags {
        self.inner.flags()
    }

    unsafe fn run_unsafe(
        &mut self,
        input: SystemIn<'_, Self>,
        world: bevy::ecs::world::unsafe_world_cell::UnsafeWorldCell,
    ) -> Result<Self::Out, bevy::ecs::system::RunSystemError> {
        let t0 = Instant::now();
        // SAFETY: repassa os mesmos contratos do sistema interior (o caller
        // garante o acesso exclusivo exigido por `initialize`).
        let out = unsafe { self.inner.run_unsafe(input, world) };
        record_system(self.group, self.key, t0.elapsed().as_secs_f32() * 1000.0);
        out
    }

    fn apply_deferred(&mut self, world: &mut World) {
        self.inner.apply_deferred(world);
    }

    fn queue_deferred(&mut self, world: bevy::ecs::world::DeferredWorld) {
        self.inner.queue_deferred(world);
    }

    fn initialize(&mut self, world: &mut World) -> bevy::ecs::query::FilteredAccessSet {
        self.inner.initialize(world)
    }

    fn check_change_tick(&mut self, check: bevy::ecs::change_detection::CheckChangeTicks) {
        self.inner.check_change_tick(check);
    }

    fn get_last_run(&self) -> bevy::ecs::change_detection::Tick {
        self.inner.get_last_run()
    }

    fn set_last_run(&mut self, last_run: bevy::ecs::change_detection::Tick) {
        self.inner.set_last_run(last_run);
    }
}

// ---------------------------------------------------------------- âncoras física

/// Instante de início do step de física (escrito pela âncora `before`).
#[derive(Resource, Default)]
pub struct PhysicsStepClock {
    start: Option<Instant>,
}

/// Âncora antes de `PhysicsSet::StepSimulation`: marca o relógio.
fn physics_step_start(mut clock: ResMut<PhysicsStepClock>) {
    clock.start = Some(Instant::now());
}

/// Âncora depois de `PhysicsSet::StepSimulation`: registra a duração do step
/// inteiro (pipeline Rapier + sistemas internos) como grupo `physics`.
fn physics_step_end(mut clock: ResMut<PhysicsStepClock>) {
    if let Some(t0) = clock.start.take() {
        record_system(
            Group::Physics,
            "physics.step",
            t0.elapsed().as_secs_f32() * 1000.0,
        );
    }
}

// ------------------------------------------------- âncoras das fases do Bevy

/// Fases do Bevy medidas por âncoras — nome publicado no profiler.
///
/// O `timed` só mede sistemas NOSSOS: no `simple-rpg` isso são ~4 ms de um
/// frame de 48, e os outros 44 eram invisíveis (a GPU mede 3 ms com
/// `VIBER_PROF_GPU=1`, portanto o frame é CPU no main schedule). Estas são as
/// fases do próprio Bevy que escalam com o nº de entidades e de VISTAS
/// (câmara + 4 cascatas + 6 faces por point light com sombra).
const PHASES: [&str; 6] = [
    "bevy.transform_propagate",
    "bevy.visibility_propagate",
    "bevy.check_visibility",
    "bevy.assign_lights_to_clusters",
    "bevy.update_light_frusta",
    "bevy.light_visibility",
];

/// Marca corrente da cadeia de fases: cada âncora regista o tempo desde a
/// marca anterior e passa a ser a nova marca.
///
/// Medir cada set com um par `before`/`after` INDEPENDENTE dá números
/// cumulativos e sobrepostos (o executor paralelo corre os `phase_start`
/// todos no início do `PostUpdate`, e o `after` de cada set fecha num
/// instante diferente — as seis leituras eram todas "desde o início do
/// PostUpdate" e somavam 27 ms num frame de 44). Com uma marca ÚNICA e
/// encadeada, cada linha é a fatia daquele set e a soma é o `PostUpdate`.
#[derive(Resource, Default)]
pub struct PhaseClocks {
    mark: Option<Instant>,
}

/// Abre a cadeia no início do `PostUpdate` (antes do primeiro set medido).
fn phase_chain_start(mut clocks: ResMut<PhaseClocks>) {
    clocks.mark = Some(Instant::now());
}

/// Fecha a fatia do set `I` e reabre a marca para o set seguinte.
fn phase_end<const I: usize>(mut clocks: ResMut<PhaseClocks>) {
    let now = Instant::now();
    if let Some(mark) = clocks.mark.replace(now) {
        record_system(
            Group::Engine,
            PHASES[I],
            now.duration_since(mark).as_secs_f32() * 1000.0,
        );
    }
}

/// Mede as fases pesadas do Bevy em `PostUpdate`, na ordem em que o Bevy as
/// encadeia (transform → visibilidade → luzes). Custa uma leitura de relógio
/// por fase e por frame.
pub fn engine_phase_anchors(app: &mut App) {
    use bevy::camera::visibility::VisibilitySystems;
    use bevy::light::SimulationLightSystems;
    use bevy::transform::TransformSystems;

    app.init_resource::<PhaseClocks>();
    app.add_systems(
        PostUpdate,
        phase_chain_start
            .before(TransformSystems::Propagate)
            .before(VisibilitySystems::VisibilityPropagate)
            .before(VisibilitySystems::CheckVisibility)
            .before(SimulationLightSystems::AssignLightsToClusters)
            .before(SimulationLightSystems::UpdateLightFrusta)
            .before(SimulationLightSystems::CheckLightVisibility),
    );
    macro_rules! anchor {
        ($index:expr, $set:expr) => {
            app.add_systems(PostUpdate, phase_end::<$index>.after($set));
        };
    }
    anchor!(0, TransformSystems::Propagate);
    anchor!(1, VisibilitySystems::VisibilityPropagate);
    anchor!(2, VisibilitySystems::CheckVisibility);
    anchor!(3, SimulationLightSystems::AssignLightsToClusters);
    anchor!(4, SimulationLightSystems::UpdateLightFrusta);
    anchor!(5, SimulationLightSystems::CheckLightVisibility);
}

// ------------------------------------------------ linha do tempo do frame

/// Fatias do frame por SCHEDULE, na ordem do `MainScheduleOrder`.
///
/// A última linha, `sched.render_wait`, é o buraco entre o fim do `Last` e o
/// início do `First` seguinte: extract + sincronização com o render app +
/// present/vsync. Num frame CPU-bound no main schedule ela é pequena; num
/// frame preso no render (ou no vsync) é ela que come o frame. Sem esta
/// linha, um frame de 44 ms com 10 ms de sistemas não tinha explicação.
const SCHEDULES: [&str; 7] = [
    "sched.first",
    "sched.pre_update",
    "sched.fixed_main",
    "sched.update",
    "sched.spawn_scene",
    "sched.post_update",
    "sched.last",
];

/// Nome da fatia entre frames (fim do `Last` → início do `First`).
const RENDER_WAIT: &str = "sched.render_wait";

#[derive(Resource, Default)]
pub struct FrameTimeline {
    mark: Option<Instant>,
}

/// Marca inserida entre schedules: fecha a fatia anterior com o nome `I`.
fn timeline_mark<const I: usize>(mut timeline: ResMut<FrameTimeline>) {
    let now = Instant::now();
    if let Some(mark) = timeline.mark.replace(now) {
        let ms = now.duration_since(mark).as_secs_f32() * 1000.0;
        // `I == SCHEDULES.len()` é a marca de abertura do frame: o que ela
        // fecha é o intervalo ENTRE frames (render/present), não um schedule.
        let name = SCHEDULES.get(I).copied().unwrap_or(RENDER_WAIT);
        record_system(Group::Engine, name, ms);
    }
}

/// Insere as marcas na ordem do `Main` — uma antes do `First` (abre o frame)
/// e uma depois de cada schedule. Cada marca é um schedule próprio com um
/// único sistema: é a única forma de garantir a ordem (dentro de um schedule
/// o executor é livre de correr as âncoras quando quiser).
pub fn frame_timeline(app: &mut App) {
    use bevy::app::{First, Last, PostUpdate, PreUpdate, RunFixedMainLoop, SpawnScene, Update};
    use bevy::ecs::schedule::ScheduleLabel;

    macro_rules! mark_schedule {
        ($name:ident) => {
            #[derive(ScheduleLabel, Clone, Debug, PartialEq, Eq, Hash)]
            struct $name;
        };
    }
    mark_schedule!(MarkFrameStart);
    mark_schedule!(MarkFirst);
    mark_schedule!(MarkPreUpdate);
    mark_schedule!(MarkFixedMain);
    mark_schedule!(MarkUpdate);
    mark_schedule!(MarkSpawnScene);
    mark_schedule!(MarkPostUpdate);
    mark_schedule!(MarkLast);

    app.init_resource::<FrameTimeline>();
    app.add_systems(MarkFrameStart, timeline_mark::<{ SCHEDULES.len() }>);
    app.add_systems(MarkFirst, timeline_mark::<0>);
    app.add_systems(MarkPreUpdate, timeline_mark::<1>);
    app.add_systems(MarkFixedMain, timeline_mark::<2>);
    app.add_systems(MarkUpdate, timeline_mark::<3>);
    app.add_systems(MarkSpawnScene, timeline_mark::<4>);
    app.add_systems(MarkPostUpdate, timeline_mark::<5>);
    app.add_systems(MarkLast, timeline_mark::<6>);

    let mut order = app
        .world_mut()
        .resource_mut::<bevy::app::MainScheduleOrder>();
    order.insert_before(First, MarkFrameStart);
    order.insert_after(First, MarkFirst);
    order.insert_after(PreUpdate, MarkPreUpdate);
    order.insert_after(RunFixedMainLoop, MarkFixedMain);
    order.insert_after(Update, MarkUpdate);
    order.insert_after(SpawnScene, MarkSpawnScene);
    order.insert_after(PostUpdate, MarkPostUpdate);
    order.insert_after(Last, MarkLast);
}

// -------------------------------------------- âncoras do RENDER APP (sub-app)

/// Fases do schedule `Render` (sub-app do render), medidas com a mesma
/// cadeia de marcas do `PostUpdate`.
///
/// Com o `PipelinedRenderingPlugin` o render app corre numa thread própria e
/// o main schedule bloqueia à espera dele — é o `sched.render_wait`. Sem
/// estas linhas, esse bloco (22 dos 30 ms do `simple-rpg` de dia) era uma
/// caixa preta: a GPU mede 7 ms nos passes, o resto é CPU aqui.
const RENDER_PHASES: [&str; 11] = [
    "render.extract_commands",
    "render.prepare_assets",
    "render.prepare_meshes",
    "render.create_views",
    "render.specialize",
    "render.prepare_views",
    "render.queue",
    "render.phase_sort",
    "render.prepare",
    "render.bind_groups",
    "render.render",
];

/// Marca da cadeia do render app (vive no mundo do sub-app).
#[derive(Resource, Default)]
pub struct RenderPhaseClock {
    mark: Option<Instant>,
}

fn render_phase_start(mut clock: ResMut<RenderPhaseClock>) {
    clock.mark = Some(Instant::now());
}

fn render_phase_end<const I: usize>(mut clock: ResMut<RenderPhaseClock>) {
    let now = Instant::now();
    if let Some(mark) = clock.mark.replace(now) {
        record_system(
            Group::Engine,
            RENDER_PHASES[I],
            now.duration_since(mark).as_secs_f32() * 1000.0,
        );
    }
}

/// Instala as âncoras no sub-app do render. Os registos são os MESMOS
/// globais do `timed` (um `Mutex` por fase por frame), por isso aparecem no
/// snapshot do profiler sem ponte nenhuma entre mundos.
pub fn render_phase_anchors(app: &mut App) {
    use bevy::render::{Render, RenderApp, RenderSystems};

    let Some(render_app) = app.get_sub_app_mut(RenderApp) else {
        return;
    };
    render_app.init_resource::<RenderPhaseClock>();
    render_app.add_systems(
        Render,
        render_phase_start.before(RenderSystems::ExtractCommands),
    );
    macro_rules! anchor {
        ($index:expr, $set:expr) => {
            render_app.add_systems(Render, render_phase_end::<$index>.after($set));
        };
    }
    anchor!(0, RenderSystems::ExtractCommands);
    anchor!(1, RenderSystems::PrepareAssets);
    anchor!(2, RenderSystems::PrepareMeshes);
    anchor!(3, RenderSystems::CreateViews);
    anchor!(4, RenderSystems::Specialize);
    anchor!(5, RenderSystems::PrepareViews);
    anchor!(6, RenderSystems::QueueSweep);
    anchor!(7, RenderSystems::PhaseSort);
    anchor!(8, RenderSystems::PrepareResourcesFlush);
    anchor!(9, RenderSystems::PrepareBindGroups);
    anchor!(10, RenderSystems::Render);
}

/// Regista as âncoras do step de física no mesmo schedule do
/// `RapierPhysicsPlugin` (PostUpdate por omissão no bevy_rapier 0.36).
pub fn physics_anchors(app: &mut App, schedule: impl bevy::ecs::schedule::ScheduleLabel + Clone) {
    use bevy_rapier3d::plugin::PhysicsSet;
    app.init_resource::<PhysicsStepClock>()
        .add_systems(
            schedule.clone(),
            physics_step_start.before(PhysicsSet::StepSimulation),
        )
        .add_systems(schedule, physics_step_end.after(PhysicsSet::StepSimulation));
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Os registos vivem em globais partilhados — os testes que os tocam
    /// correm em série (cargo test lança testes em threads paralelas).
    static TEST_LOCK: Mutex<()> = Mutex::new(());

    fn ring_with(ms: &[f32]) -> Ring {
        let mut ring = Ring::default();
        for &v in ms {
            ring.push(v);
        }
        ring
    }

    #[test]
    fn test_ring_stats_basic() {
        let ring = ring_with(&[10.0, 20.0, 30.0, 40.0]);
        let (avg, min, max, p95) = ring.stats();
        assert!((avg - 25.0).abs() < 1e-4);
        assert!((min - 10.0).abs() < 1e-4);
        assert!((max - 40.0).abs() < 1e-4);
        assert!(
            (p95 - 40.0).abs() < 1e-4,
            "p95 de 4 amostras = max, got {p95}"
        );
    }

    #[test]
    fn test_ring_p95_intermediate() {
        // 100 amostras: p95 = 95.º valor ordenado (índice 94).
        let mut ms = vec![1.0f32; 100];
        for (i, sample) in ms.iter_mut().enumerate().take(100).skip(90) {
            *sample = i as f32;
        }
        let ring = ring_with(&ms);
        let (_, _, _, p95) = ring.stats();
        assert!((p95 - 94.0).abs() < 1e-4, "got {p95}");
    }

    #[test]
    fn test_ring_empty() {
        let ring = Ring::default();
        assert_eq!(ring.stats(), (0.0, 0.0, 0.0, 0.0));
    }

    #[test]
    fn test_ring_evicts_old_samples() {
        let mut ring = Ring::default();
        ring.push(50.0);
        for _ in 0..RING_SIZE + 10 {
            ring.push(1.0);
        }
        let (avg, ..) = ring.stats();
        assert!((avg - 1.0).abs() < 1e-4, "janela evicta amostras antigas");
        assert_eq!(ring.filled, RING_SIZE);
    }

    #[test]
    fn test_record_and_snapshot() {
        let _guard = TEST_LOCK.lock();
        reset_timings();
        set_frozen(false);
        record_system(Group::Combat, "test_sys_a", 2.0);
        record_system(Group::Combat, "test_sys_a", 4.0);
        record_system(Group::Hud, "test_sys_b", 0.5);
        let rows = systems_snapshot(16.0);
        assert_eq!(rows.len(), 2, "{rows:?}");
        assert_eq!(rows[0].name, "test_sys_a");
        assert!((rows[0].avg_ms - 3.0).abs() < 1e-4);
        assert_eq!(rows[0].group, "combat");
        assert!((rows[0].pct - 3.0 / 16.0 * 100.0).abs() < 1e-3);

        let groups = groups_snapshot(16.0);
        let combat = groups.iter().find(|(g, _)| *g == Group::Combat).unwrap();
        assert!((combat.1.avg_ms - 3.0).abs() < 1e-4);
    }

    #[test]
    fn test_frozen_freezes_acquisition_but_keeps_rings() {
        let _guard = TEST_LOCK.lock();
        reset_timings();
        set_frozen(false);
        record_system(Group::World, "test_frozen_sys", 1.0);
        set_frozen(true);
        record_system(Group::World, "test_frozen_sys", 99.0);
        record_script("frozen.lua", 99.0);
        let rows = systems_snapshot(16.0);
        assert_eq!(rows.len(), 1);
        assert!(
            (rows[0].avg_ms - 1.0).abs() < 1e-4,
            "registo congelado ignorado"
        );
        assert!(scripts_snapshot(16.0).is_empty());
        set_frozen(false);
    }

    #[test]
    fn test_non_finite_ignored() {
        let _guard = TEST_LOCK.lock();
        reset_timings();
        set_frozen(false);
        record_system(Group::Fx, "test_nan_sys", f32::NAN);
        record_system(Group::Fx, "test_nan_sys", f32::INFINITY);
        assert!(systems_snapshot(16.0).is_empty());
    }

    #[test]
    fn test_script_rings_dedupe_by_path() {
        let _guard = TEST_LOCK.lock();
        reset_timings();
        set_frozen(false);
        record_script("slime.lua", 0.2);
        record_script("slime.lua", 0.4);
        record_script("well.lua", 0.1);
        let rows = scripts_snapshot(16.0);
        assert_eq!(rows.len(), 2, "{rows:?}");
        assert_eq!(rows[0].name, "slime.lua");
        assert!((rows[0].avg_ms - 0.3).abs() < 1e-4);
        assert_eq!(rows[0].group, "scripts");
    }

    #[test]
    fn test_reset_clears_everything() {
        let _guard = TEST_LOCK.lock();
        reset_timings();
        set_frozen(false);
        record_system(Group::Terrain, "test_reset_sys", 1.0);
        record_script("reset.lua", 1.0);
        reset_timings();
        assert!(systems_snapshot(16.0).is_empty());
        assert!(scripts_snapshot(16.0).is_empty());
    }
}
