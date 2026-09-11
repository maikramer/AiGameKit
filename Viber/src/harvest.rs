//! Colheita NATIVA — port do plugin `destructible` do VibeGame
//! (`src/plugins/destructible/{systems,fx}.ts`).
//!
//! Props marcados com `destructible="…"` (árvores/rochas dos spawners) são
//! colhíveis: o press de ataque ([J]/clique esquerdo) com um prop em alcance
//! comete um golpe que aterra a [`SWING_IMPACT_FRACTION`] do clip
//! `mine`/`chop` do rig do herói (a 1.4×); no último golpe o prop quebra com
//! o FX do [`BreakStyle`]:
//!
//! - **Fall** (árvores): a malha filha `Top` (os GLBs vêm pré-divididos em
//!   `Stump`/`Top`) é re-parentada a um pivô no PLANO DE CORTE (base da peça,
//!   lida do AABB dos meshes — a origem do nó fica no meio e deixava a árvore
//!   "caída no alto") e tomba 84.6° na direção herói→prop (ease-in `t^2.4`),
//!   com bursts de dust+leaves no impacto e fade do topo — o `Stump` FICA na
//!   entidade original (toco persistente com o collider), igual à referência.
//! - **Shatter** (rochas): 9 pedaços icosaédricos flat-shaded voam em
//!   balística (gravidade 20), pousam, esperam e fazem fade.
//! - **Burst**: só o burst de partículas.
//!
//! Por golpe não-final: burst do `hit-preset`, SFX (`MineHit`/`ChopHit`),
//! wobble do visual ([`HitShake`]) e escurecimento progressivo dos materiais
//! (aproximação do crack overlay da referência — ×0.85 por golpe).
//! No break: burst do `preset`, popup flutuante ([`DamageNumberEvent`]),
//! loot no vault (mesmo caminho de `viber.report_collect`, logo os objetivos
//! `collect` das quests contam), +[`HARVEST_XP`] XP e SFX de quebra.
//!
//! A ferramenta troca sozinha na mão do herói ([`harvest_tool_system`]):
//! Chop→`felling_axe`, Mine→`pickaxe`, sem alvo→arma de
//! [`crate::combat::WEAPON_TABLE`] (grips do `held-items.json`). O melee
//! cede: com [`HarvestContext::target`] ativo o press inteiro pertence à
//! colheita (guard em [`crate::combat::player_melee_attack`]) — nunca
//! co-disparam.
//!
//! **Supressão dos scripts Lua do prop**: os `tree.lua`/`rock.lua` antigos
//! continuam nos XMLs (a limpeza é outra tarefa), mas no 1.º golpe nativo o
//! [`harvest_attack_system`] remove `LuaScriptRef`/`ScriptInteraction` do
//! prop — os contadores/toasts/`topple` do script deixam de correr a meio da
//! colheita nativa (sem dupla contagem nem XP duplicado).
//!
//! **Desvios conhecidos vs referência** (documentados, como no terreno):
//! - Crack overlay shader (voronoi/vertical) → darken progressivo ×0.85 por
//!   golpe com o mesmo papel visual (o `crack-style` do XML é aceite e
//!   mapeado para o mesmo darken — rocha e madeira partilham o efeito).
//! - Pedra partida: a cor dos pedaços deriva do `popup-color` ×0.85 (a
//!   referência amostra o 1.º material do prop; ler materiais GLTF crus não
//!   é fiável aqui).
//! - Presets de hit mapeados para a biblioteca local (`particles::preset`):
//!   `rockshards`→`sparks`, `woodchips`→`leaves`, `dust`→`ground-dust`.
//! - Árvore a cair: sem clipping plane — a divisão Stump/Top vem pronta nos
//!   GLBs; sem `Top` no prop, o Fall cai para o burst simples.
//! - Popup usa o pool de números de dano do feedback (texto e cor
//!   arbitrários, tamanho fixo do pool — a referência usa 0.4 world units).
//! - Cooldown do golpe 0.4 s conta do PRESS (idem à referência); o impacto é
//!   conduzido por [`PendingHarvest`] como o `pendingImpact` de lá.

use bevy::ecs::message::MessageWriter;
use bevy::ecs::system::SystemParam;
use bevy::input::mouse::MouseButton;
use bevy::math::primitives::Sphere;
use bevy::prelude::*;

use crate::ambient::{SfxClip, SfxEvent};
use crate::combat::{ATTACK_TIME_SCALE, HeldWeapon, SWING_IMPACT_FRACTION};
use crate::economy::Vault;
use crate::feedback::DamageNumberEvent;
use crate::luau::ScriptInteraction;
use crate::profiler::{Group, timed};
use crate::recipes::{BreakStyleSpec, DestructibleSpec};
use crate::spawner::Rng;
use crate::terrain::runtime::TerrainRuntime;
use crate::ui::collect::UiPrompt;
use crate::vitals::{Xp, gain_xp};

// ── constantes (valores da referência VibeGame) ─────────────────────────

/// Cadência dos golpes de colheita (s) — `SWING_COOLDOWN_SEC` 0.4.
pub const SWING_COOLDOWN: f32 = 0.4;
/// Fração do clip em que o golpe aterra (o melee usa a mesma).
pub const IMPACT_DELAY_MIN: f32 = 0.12;
/// Teto do atraso de impacto (s).
pub const IMPACT_DELAY_MAX: f32 = 0.45;
/// Impacto sem duração de clip conhecida (s).
pub const FALLBACK_IMPACT_DELAY: f32 = 0.22;
/// Alcance máximo do prompt/alvo (o `range` do prop é capado aqui).
pub const PROMPT_RANGE_CAP: f32 = 3.5;
/// XP por prop quebrado (igual aos scripts tree/rock.lua que substitui).
pub const HARVEST_XP: u32 = 30;

/// Wobble por golpe: duração (s) — `startHitShake` 0.4.
pub const SHAKE_DURATION: f32 = 0.4;
/// Amplitude do wobble (rad) — 0.045.
pub const SHAKE_AMP: f32 = 0.045;
/// Frequência da fase do wobble (rad/s) — `elapsed * 34`.
pub const SHAKE_PHASE_HZ: f32 = 34.0;

/// Queda da árvore: duração (s) — `FALL_DURATION` 1.0.
pub const FALL_DURATION: f32 = 1.0;
/// Ângulo final da queda (rad) — `FALL_MAX_ANGLE` = π·0.47 ≈ 84.6°.
pub const FALL_MAX_ANGLE: f32 = std::f32::consts::PI * 0.47;
/// Pausa no chão antes do fade (s) — `FALL_TOP_HOLD` 0.3.
pub const FALL_IMPACT_HOLD: f32 = 0.3;
/// Fade do topo no chão (s) — `FALL_FADE` 0.7.
pub const FALL_FADE: f32 = 0.7;
/// Fração do comprimento do topo onde o crash aterra (bursts).
pub const FALL_IMPACT_FRACTION: f32 = 0.75;

/// Estilhaços da rocha: nº de pedaços — `SHATTER_PIECES` 9.
pub const SHATTER_PIECES: usize = 9;
/// Gravidade dos pedaços (m/s²) — `SHATTER_GRAVITY` 20.
pub const SHATTER_GRAVITY: f32 = 20.0;
/// Espera no chão antes do fade (s) — `SHATTER_HOLD` 1.2.
pub const SHATTER_HOLD: f32 = 1.2;
/// Fade dos pedaços (s) — `SHATTER_FADE` 0.6.
pub const SHATTER_FADE: f32 = 0.6;

// ── componentes / recursos ──────────────────────────────────────────────

/// Estilo de quebra (runtime, resolvido do [`DestructibleSpec`]).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum BreakStyle {
    /// Só o burst de partículas (default).
    #[default]
    Burst,
    /// Árvore tomba; toco persiste.
    Fall,
    /// Rocha estilhaça em pedaços.
    Shatter,
}

/// Prop destrutível/colhível (port do `Destructible` da referência).
///
/// Inserido pelo spawner nas instâncias com `destructible` no template e
/// pelo spawn em entidades standalone com o attr universal.
#[derive(Debug, Clone, Component)]
pub struct Destructible {
    /// Golpes até quebrar.
    pub hits: u32,
    /// Golpes levados até agora.
    pub hits_taken: u32,
    /// Alcance do golpe (m).
    pub range: f32,
    /// FX de quebra.
    pub break_style: BreakStyle,
    /// Preset do burst de break.
    pub preset: String,
    /// Partículas do burst de break.
    pub burst_count: u32,
    /// Preset do burst por golpe.
    pub hit_preset: String,
    /// Partículas do burst por golpe.
    pub hit_burst_count: u32,
    /// Wobble do visual por golpe.
    pub shake_on_hit: bool,
    /// Popup flutuante no break (`None` = sem popup).
    pub popup: Option<String>,
    /// Cor do popup.
    pub popup_color: Color,
    /// `(kind, yield)` do `<ResourceNode>` — loot no vault; `None` = só XP.
    pub resource: Option<(String, u32)>,
}

impl Destructible {
    /// Resolve o spec do XML nos valores runtime (defaults da referência).
    pub fn from_spec(spec: &DestructibleSpec) -> Self {
        Self {
            hits: spec.hits.unwrap_or(3).max(1),
            hits_taken: 0,
            range: spec.range.filter(|r| *r > 0.0).unwrap_or(3.5),
            break_style: match spec.break_style {
                BreakStyleSpec::Burst => BreakStyle::Burst,
                BreakStyleSpec::Fall => BreakStyle::Fall,
                BreakStyleSpec::Shatter => BreakStyle::Shatter,
            },
            preset: map_preset(spec.preset.as_deref().unwrap_or("dust")),
            burst_count: spec.burst_count.unwrap_or(60).max(1),
            hit_preset: map_preset(spec.hit_preset.as_deref().unwrap_or("sparks")),
            hit_burst_count: spec.hit_burst_count.unwrap_or(15).max(1),
            shake_on_hit: spec.shake_on_hit,
            popup: spec.popup_text.clone().filter(|t| !t.trim().is_empty()),
            popup_color: spec
                .popup_color
                .map(|[r, g, b]| Color::srgb(r, g, b))
                .unwrap_or(Color::WHITE),
            resource: spec.resource.clone(),
        }
    }
}

/// Mapeia os presets da referência para os presets da biblioteca de
/// partículas local (`particles::preset`); os outros passam intactos.
pub fn map_preset(name: &str) -> String {
    match name {
        "rockshards" => "sparks".to_string(),
        "woodchips" => "leaves".to_string(),
        "dust" => "ground-dust".to_string(),
        other => other.to_string(),
    }
}

/// Ferramenta que o golpe de colheita pede (`Fall`→Chop, resto→Mine).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HarvestKind {
    Mine,
    Chop,
}

/// Estilo → ferramenta.
pub fn kind_for(style: BreakStyle) -> HarvestKind {
    match style {
        BreakStyle::Fall => HarvestKind::Chop,
        _ => HarvestKind::Mine,
    }
}

/// Rótulo do prompt por ferramenta.
pub fn prompt_label(kind: HarvestKind) -> &'static str {
    match kind {
        HarvestKind::Mine => "Minerar",
        HarvestKind::Chop => "Cortar",
    }
}

/// Clip de animação por ferramenta (o rig do herói tem os dois).
pub fn clip_for(kind: HarvestKind) -> &'static str {
    match kind {
        HarvestKind::Mine => "mine",
        HarvestKind::Chop => "chop",
    }
}

/// SFX de golpe por ferramenta.
pub fn sfx_hit(kind: HarvestKind) -> SfxClip {
    match kind {
        HarvestKind::Mine => SfxClip::MineHit,
        HarvestKind::Chop => SfxClip::ChopHit,
    }
}

/// SFX de quebra por ferramenta.
pub fn sfx_break(kind: HarvestKind) -> SfxClip {
    match kind {
        HarvestKind::Mine => SfxClip::MineBreak,
        HarvestKind::Chop => SfxClip::ChopBreak,
    }
}

/// Alvo de colheita atual do herói (recalculado por frame por
/// [`harvest_context_system`]) + relógio do último press (cooldown).
#[derive(Debug, Clone, Copy, Resource, Default)]
pub struct HarvestContext {
    /// `(prop, ferramenta)` mais próximo dentro do alcance; `None` = sem.
    pub target: Option<(Entity, HarvestKind)>,
    /// Timestamp do último press de colheita (elapsed da engine, s).
    pub last_swing: f64,
}

/// Golpe de colheita em curso: o press cria, [`harvest_impact_system`]
/// conduz até ao impacto (o `pendingImpact` da referência).
#[derive(Debug, Clone, Copy, Resource, Default)]
pub struct PendingHarvest {
    pub active: bool,
    pub target: Option<Entity>,
    /// Wall-clock virtual até ao golpe (s).
    pub delay: f32,
}

/// Ferramenta atualmente na mão por causa da colheita (`None` = arma do
/// [`crate::combat::WEAPON_TABLE`]) — evita re-anexos por frame.
#[derive(Debug, Clone, Copy, PartialEq, Resource, Default)]
pub struct HarvestTool {
    pub active: Option<HarvestKind>,
}

/// Wobble rotacional decrescente nos filhos visuais do prop
/// (`startHitShake` da referência: rotação local x/z, restaura no fim).
#[derive(Debug, Clone, Component)]
pub struct HitShake {
    pub elapsed: f32,
    /// Filhos abanados + rotação local original.
    pub targets: Vec<(Entity, Quat)>,
}

/// Escurecimento progressivo por golpe (aproximação do crack overlay):
/// clones dos materiais do prop vivem no componente, a base original fica
/// guardada para o fator ser `base × 0.85^hits` sem acumular erro.
#[derive(Debug, Clone, Component)]
pub struct Darken {
    /// Cor base do 1.º material original.
    pub base: Color,
    /// Handles clonados (ativos enquanto o prop vive).
    pub clones: Vec<Handle<StandardMaterial>>,
}

/// Pedaço de rocha em balística (`startRockShatter`).
#[derive(Debug, Clone, Component)]
pub struct DebrisChunk {
    pub vel: Vec3,
    /// Velocidade angular (rad/s) por eixo.
    pub angvel: Vec3,
    /// Metade da altura do pedaço — rest = ground + rest_half.
    pub rest_half: f32,
    /// Chão do prop (fallback sem terrain runtime).
    pub ground_hint: f32,
    pub landed: bool,
    pub elapsed: f32,
    /// Material partilhado dos pedaços (fade final).
    pub material: Handle<StandardMaterial>,
}

/// Queda da árvore (pivô com o `Top` re-parentado).
#[derive(Debug, Clone, Component)]
pub struct TreeFall {
    /// Eixo da queda: `(dir.z, 0, −dir.x)` com dir = herói→prop.
    pub axis: Vec3,
    /// Direção XZ unitária da queda (para os bursts de impacto).
    pub dir: Vec3,
    /// Rotação do pivô no handoff (a do topo no mundo).
    pub initial: Quat,
    pub elapsed: f32,
    pub impact_done: bool,
    /// Y do terreno sob o prop.
    pub ground_y: f32,
    /// Ponto de corte (world translation do `Top`).
    pub cut_point: Vec3,
    /// Altura aproximada do topo (cut_point.y − ground).
    pub top_length: f32,
    /// Materiais clonados do topo (fade).
    pub top_materials: Vec<Handle<StandardMaterial>>,
}

// ── lógica pura (testada) ───────────────────────────────────────────────

/// Atraso do impacto a partir da duração do clip e da speed de playback —
/// `dur/speed·0.35` clamp 0.12–0.45; sem duração conhecida, fallback.
pub fn impact_delay(clip_duration: f32, speed: f32) -> f32 {
    if clip_duration > 1e-3 && speed > 0.05 {
        (clip_duration * SWING_IMPACT_FRACTION / speed).clamp(IMPACT_DELAY_MIN, IMPACT_DELAY_MAX)
    } else {
        FALLBACK_IMPACT_DELAY
    }
}

/// Ângulo da queda no instante `t` (rad): ease-in `t^2.4` até
/// [`FALL_MAX_ANGLE`] (≈84.6°), clampeado.
pub fn fall_angle_rad(t: f32, duration: f32) -> f32 {
    let k = (t / duration.max(f32::EPSILON)).clamp(0.0, 1.0);
    FALL_MAX_ANGLE * k.powf(2.4)
}

/// Multiplica o RGB de uma cor por `factor` (canal alpha intacto).
pub fn darken_color(color: Color, factor: f32) -> Color {
    let mut srgba = color.to_srgba();
    srgba.red = (srgba.red * factor).clamp(0.0, 1.0);
    srgba.green = (srgba.green * factor).clamp(0.0, 1.0);
    srgba.blue = (srgba.blue * factor).clamp(0.0, 1.0);
    Color::Srgba(srgba)
}

/// Fator de escurecimento após `hits` golpes (0.85 por golpe, cap 8).
pub fn darken_factor(hits_taken: u32) -> f32 {
    0.85_f32.powi(hits_taken.min(8) as i32)
}

/// Escala de um pedaço: `(0.1 + rnd·0.16) · max(scale, 0.4)` com variação
/// por eixo 0.6–1.4 (valores da referência).
pub fn debris_scale(rng: &mut Rng, scale: f32) -> Vec3 {
    let s = (0.1 + rng.next_f32() * 0.16) * scale.max(0.4);
    Vec3::new(
        s * rng.range(0.6, 1.4),
        s * rng.range(0.6, 1.4),
        s * rng.range(0.6, 1.4),
    )
}

/// Velocidade inicial de um pedaço: horizontal 1.5–5 m/s + vy 2.5–6.
pub fn debris_velocity(rng: &mut Rng) -> Vec3 {
    let angle = rng.range(0.0, std::f32::consts::TAU);
    let speed = 1.5 + rng.next_f32() * 3.5;
    Vec3::new(
        angle.sin() * speed,
        2.5 + rng.next_f32() * 3.5,
        angle.cos() * speed,
    )
}

/// Velocidade angular ±5 rad/s por eixo.
pub fn debris_angvel(rng: &mut Rng) -> Vec3 {
    Vec3::new(
        (rng.next_f32() - 0.5) * 10.0,
        (rng.next_f32() - 0.5) * 10.0,
        (rng.next_f32() - 0.5) * 10.0,
    )
}

/// Deposita o loot no vault — MESMO caminho de `viber.report_collect`
/// (recurso nomeado; itens de objetivo caem no `item_add`). Os objetivos
/// `collect` das quests leem o vault, logo progridem sozinhos.
pub fn grant_loot(vault: &mut Vault, resource: Option<&(String, u32)>) {
    if let Some((kind, amount)) = resource {
        if !vault.add_resource(kind, *amount) {
            vault.item_add(kind, *amount);
        }
    }
}

// ── plugin ──────────────────────────────────────────────────────────────

pub struct HarvestPlugin;

/// Conjunto dos sistemas de colheita — permite ordenar OUTROS sistemas contra
/// eles (ex.: o melee) sem re-adicionar `harvest_context_system` (adicionar
/// duas vezes o mesmo fn cria duas instâncias no schedule e o pânico
/// "more than one instance" ao inicializar).
#[derive(Debug, Hash, PartialEq, Eq, Clone, bevy::ecs::schedule::SystemSet)]
pub struct HarvestSet;

impl bevy::app::Plugin for HarvestPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        // Idempotente com o UiPlugin/CombatPlugin: o melee lê o contexto e o
        // prompt é o mesmo recurso do collect (apps mínimas incluídas).
        app.init_resource::<HarvestContext>()
            .init_resource::<PendingHarvest>()
            .init_resource::<HarvestTool>()
            .init_resource::<UiPrompt>()
            .add_message::<SfxEvent>()
            .add_message::<DamageNumberEvent>()
            // context → tool → attack → impact: o mesmo press nunca gera
            // dois alvos nem perde o golpe cometido. A chain inteira fica
            // DEPOIS do UiSet::Collect (o contexto pina o prompt por cima do
            // valor fresco do `collect_ui_prompt`).
            .add_systems(
                bevy::app::Update,
                (
                    harvest_context_system,
                    harvest_tool_system,
                    timed(Group::Combat, harvest_attack_system),
                    timed(Group::Combat, harvest_impact_system),
                )
                    .chain()
                    .in_set(HarvestSet)
                    .after(crate::ui::UiSet::Collect),
            )
            .add_systems(
                bevy::app::Update,
                (hit_shake_system, debris_chunk_system, tree_fall_system).in_set(HarvestSet),
            );
    }
}

// ── parâmetros partilhados dos FX ───────────────────────────────────────

#[derive(SystemParam)]
pub struct HarvestFx<'w, 's> {
    commands: Commands<'w, 's>,
    meshes: ResMut<'w, Assets<Mesh>>,
    materials: ResMut<'w, Assets<StandardMaterial>>,
    numbers: MessageWriter<'w, DamageNumberEvent>,
    sfx: MessageWriter<'w, SfxEvent>,
    names: Query<'w, 's, &'static Name>,
    children: Query<'w, 's, &'static Children>,
    mesh_mats: Query<'w, 's, &'static MeshMaterial3d<StandardMaterial>>,
    /// AABB da peça `Top` para o pivô da queda (plano de corte).
    mesh_viz: Query<'w, 's, &'static Mesh3d>,
    /// `Without<Player>`: os queries de Transform do herói (mut) têm de ser
    /// disjuntos dos que tocam nos filhos dos props.
    transforms: Query<'w, 's, &'static Transform, Without<crate::player::Player>>,
    globals: Query<'w, 's, &'static GlobalTransform>,
}

// ── 1. alvo + prompt ────────────────────────────────────────────────────

/// Prop destrutível NÃO-quebrado mais próximo dentro de `range.min(3.5)`.
/// Um [`ScriptInteraction`] (NPC/colheita scriptada) noutra entidade MAIS
/// PERTO ganha o [J] — o alvo nativo só assume quando é o mais próximo.
/// Escreve o prompt ([`UiPrompt`]) sobre o valor fresco do
/// `collect_ui_prompt` (a constraint de ordem fica no `main.rs`).
#[allow(clippy::type_complexity)]
pub fn harvest_context_system(
    mut context: ResMut<HarvestContext>,
    mut prompt: ResMut<UiPrompt>,
    players: Query<&GlobalTransform, With<crate::player::Player>>,
    props: Query<(Entity, &GlobalTransform, &Destructible), Without<crate::player::Player>>,
    interactions: Query<(&GlobalTransform, &ScriptInteraction), Without<crate::player::Player>>,
) {
    let Ok(player) = players.single() else {
        context.target = None;
        return;
    };
    let origin = player.translation();
    let mut best: Option<(Entity, f32, HarvestKind)> = None;
    for (entity, transform, destructible) in &props {
        let dx = transform.translation().x - origin.x;
        let dz = transform.translation().z - origin.z;
        // Comparação em quadrados: o sqrt só corre no candidato aceite.
        let dist_sq = dx * dx + dz * dz;
        let range = destructible.range.min(PROMPT_RANGE_CAP);
        if dist_sq > range * range || dist_sq < 1e-6 {
            continue;
        }
        let dist = dist_sq.sqrt();
        if best.is_none_or(|(_, d, _)| dist < d) {
            best = Some((entity, dist, kind_for(destructible.break_style)));
        }
    }
    // Ceder a interações scriptadas mais próximas (mesma tecla J).
    if let Some((_, prop_dist, _)) = best {
        for (transform, interaction) in &interactions {
            if interaction.key != KeyCode::KeyJ {
                continue;
            }
            let dist = transform.translation().distance(origin);
            if dist < prop_dist && dist <= interaction.range {
                best = None;
                break;
            }
        }
    }
    context.target = best.map(|(entity, _, kind)| (entity, kind));
    // Só ESCREVE com alvo — sem alvo, o valor do collect_ui_prompt deste
    // frame (fresco, corre antes) é o certo e não se toca nele.
    if let Some((_, kind)) = context.target {
        prompt.key = "J".to_string();
        prompt.label = prompt_label(kind).to_string();
    }
}

// ── 2. ferramenta na mão ────────────────────────────────────────────────

/// (url, pos, rot XYZ rad) — grips do `held-items.json` da referência.
const FELLING_AXE: (&str, [f32; 3], [f32; 3]) = (
    "assets/meshes/props/felling_axe_lod0.glb",
    [0.08, 0.05, 0.01],
    [5.84, 9.2, -0.61],
);
const PICKAXE: (&str, [f32; 3], [f32; 3]) = (
    "assets/meshes/props/pickaxe_lod0.glb",
    [0.06, 0.05, 0.07],
    [-5.32, 11.38, 2.01],
);

/// Troca o item na mão do herói quando o alvo de colheita muda: Chop→
/// felling_axe, Mine→pickaxe, nenhum→arma atual de [`WEAPON_TABLE`].
/// Reusa o osso/attach de [`crate::combat::cycle_weapon`] (o [V] está
/// bloqueado enquanto há alvo — os dois nunca disputam a mão).
#[allow(clippy::type_complexity)]
pub fn harvest_tool_system(
    mut commands: Commands,
    asset_server: Res<AssetServer>,
    context: Res<HarvestContext>,
    mut tool: ResMut<HarvestTool>,
    mut held: ResMut<HeldWeapon>,
) {
    let wanted = context.target.map(|(_, kind)| kind);
    if tool.active == wanted {
        return;
    }
    // O osso é descoberto pelo cycle_weapon; sem ele (GLB ainda a chegar),
    // tenta de novo no frame seguinte.
    let Some(bone) = held.bone else {
        return;
    };
    if let Some(old) = held.current.take() {
        commands.entity(old).despawn();
    }
    let (url, pos, rot) = match wanted {
        Some(HarvestKind::Chop) => FELLING_AXE,
        Some(HarvestKind::Mine) => PICKAXE,
        None => {
            // Restaura a arma do herói (mesma via do cycle_weapon).
            let (url, pos, rot, _scale, _label) =
                crate::combat::WEAPON_TABLE[held.idx.min(crate::combat::WEAPON_TABLE.len() - 1)];
            (url, pos, rot)
        }
    };
    let handle = crate::meshopt::load_gltf(&asset_server, url.to_owned());
    let mut transform = Transform::from_translation(Vec3::new(pos[0], pos[1], pos[2]));
    transform.rotation = Quat::from_euler(EulerRot::XYZ, rot[0], rot[1], rot[2]);
    let spawned = commands
        .spawn((
            transform,
            Visibility::Inherited,
            crate::recipes::spawn::GltfScenePending { handle },
        ))
        .id();
    commands.entity(bone).add_child(spawned);
    held.current = Some(spawned);
    tool.active = wanted;
}

// ── 3. press do golpe ───────────────────────────────────────────────────

/// [J]/clique esquerdo com alvo de colheita: cooldown 0.4 s, snap do yaw do
/// herói para o prop, clip `mine`/`chop` a 1.4× ±8 % e [`PendingHarvest`].
/// O melee está suprimido ([`crate::combat::player_melee_attack`]) — os
/// dois nunca co-disparam no mesmo press.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
pub fn harvest_attack_system(
    mut commands: Commands,
    keys: Res<ButtonInput<KeyCode>>,
    mouse: Res<ButtonInput<MouseButton>>,
    menus: Res<crate::menus::MenusOpen>,
    time: Res<Time>,
    mut context: ResMut<HarvestContext>,
    mut pending: ResMut<PendingHarvest>,
    mut players: Query<(&GlobalTransform, &mut Transform), With<crate::player::Player>>,
    props: Query<&GlobalTransform, With<Destructible>>,
    mut hero_animator: Query<&mut crate::animation::CharacterAnimator, With<crate::player::Player>>,
    mut animation_players: crate::animation::PlayerQuery,
) {
    if menus.any() {
        return;
    }
    let Some((target, kind)) = context.target else {
        return;
    };
    if !(keys.just_pressed(KeyCode::KeyJ) || mouse.just_pressed(MouseButton::Left)) {
        return;
    }
    // Cooldown: conta do press (0.4 s), igual ao `lastSwingAt` da referência.
    let elapsed = time.elapsed_secs_f64();
    if context.last_swing > 0.0 && elapsed - context.last_swing < SWING_COOLDOWN as f64 {
        return;
    }
    let Ok((player_global, mut player_transform)) = players.single_mut() else {
        return;
    };
    let Some(prop_pos) = props.get(target).ok().map(|g| g.translation()) else {
        // Alvo despawnou este frame: o contexto refresca no próximo.
        context.target = None;
        return;
    };
    context.last_swing = elapsed;
    // Supressão dos scripts Lua do prop: com o 1.º golpe nativo a colheita
    // assume o comportamento — sem contadores/toasts/topple concorrentes do
    // tree.lua/rock.lua até a tarefa de limpeza os remover dos XMLs.
    commands
        .entity(target)
        .remove::<crate::luau::LuaScriptRef>()
        .remove::<ScriptInteraction>();
    // Snap do yaw para o prop (o golpe lê-se como mirado).
    let dir = (prop_pos - player_global.translation())
        .with_y(0.0)
        .normalize_or_zero();
    if dir.length_squared() > 1e-6 {
        player_transform.rotation = crate::player::facing_rotation(dir);
    }
    // Clip `mine`/`chop` a 1.4× ±8 % (jitter do melee).
    let speed = ATTACK_TIME_SCALE * (0.94 + 0.12 * crate::combat::pseudo_roll(&time));
    let mut delay = FALLBACK_IMPACT_DELAY;
    if let Ok(mut animator) = hero_animator.single_mut() {
        let clip = clip_for(kind);
        if let Some(node) = animator.node_matching(|n| n == clip) {
            let duration = animator.duration_of(node);
            crate::animation::play_action_scaled(
                &mut animator,
                &mut animation_players,
                node,
                crate::animation::ACTION_BLEND,
                false,
                speed,
            );
            delay = impact_delay(duration, speed);
        }
    }
    *pending = PendingHarvest {
        active: true,
        target: Some(target),
        delay,
    };
}

// ── 4. impacto ──────────────────────────────────────────────────────────

/// Conduz [`PendingHarvest`]: no impacto, golpe não-final → burst de hit +
/// SFX + shake + darken e `hits_taken += 1`; golpe final → [`break_prop`]
/// (FX do estilo + burst + popup + loot + XP + SFX de quebra).
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
pub fn harvest_impact_system(
    time: Res<Time>,
    terrain: Option<Res<TerrainRuntime>>,
    mut pending: ResMut<PendingHarvest>,
    players: Query<&GlobalTransform, With<crate::player::Player>>,
    mut targets: Query<(Entity, &GlobalTransform, &mut Destructible)>,
    mut hero_xp: Query<&mut Xp, With<crate::player::Player>>,
    darkens: Query<&Darken>,
    mut vault: Option<ResMut<Vault>>,
    mut fx: HarvestFx,
) {
    if !pending.active {
        return;
    }
    pending.delay -= time.delta_secs();
    if pending.delay > 0.0 {
        return;
    }
    pending.active = false;
    let Some(target) = pending.target.take() else {
        return;
    };
    let Ok((entity, prop_global, mut destructible)) = targets.get_mut(target) else {
        return; // quebrou por outra via (script) entretanto
    };
    let Ok(player_pos) = players.single().map(|g| g.translation()) else {
        return;
    };
    let prop_pos = prop_global.translation();
    let kind = kind_for(destructible.break_style);
    let scale = prop_global.scale().max_element();
    // Ponto de impacto: lado virado ao herói, à altura do golpe — o burst
    // não nasce dentro (nem atrás) do tronco, igual ao `impactPoint` da
    // referência.
    let to_player = (player_pos - prop_pos).with_y(0.0).normalize_or_zero();
    let offset = (0.45 * scale).clamp(0.3, 1.2);
    let impact = Vec3::new(
        prop_pos.x + to_player.x * offset,
        prop_pos.y + 1.0,
        prop_pos.z + to_player.z * offset,
    );

    let breaking = destructible.hits_taken + 1 >= destructible.hits;
    let data = destructible.clone();
    if breaking {
        // FX de quebra por estilo — no Fall a entidade MANTÉM-SE como toco
        // (devolve false); burst/shatter despawnam (true).
        let _despawned = break_prop(
            &mut fx,
            &data,
            entity,
            prop_pos,
            player_pos,
            terrain.as_deref(),
        );
        // Loot no vault (report_collect) + XP +30 (mesma via de add_xp).
        if let Some(vault) = vault.as_deref_mut() {
            grant_loot(vault, data.resource.as_ref());
        }
        if let Ok(mut xp) = hero_xp.single_mut() {
            gain_xp(&mut xp, HARVEST_XP);
        }
    } else {
        destructible.hits_taken += 1;
        crate::particles::spawn_burst(
            &mut fx.commands,
            &hit_burst_spec(&data.hit_preset),
            impact,
            data.hit_burst_count as usize,
        );
        fx.sfx.write(SfxEvent {
            clip: sfx_hit(kind),
            position: Some(prop_pos),
        });
        if data.shake_on_hit {
            if let Some(shake) = build_hit_shake(&fx, entity) {
                fx.commands.entity(entity).insert(shake);
            }
        }
        apply_darken(&mut fx, &darkens, entity, destructible.hits_taken);
    }
}

/// Spec de burst de colheita (preset puro, sem overrides autorais).
fn hit_burst_spec(preset: &str) -> crate::recipes::ParticleSpec {
    crate::recipes::ParticleSpec {
        preset: preset.to_string(),
        emission_rate: None,
        life: None,
        speed: None,
        size: None,
        color: None,
        shape_radius: None,
        looping: false,
        world_space: false,
    }
}

/// FX de quebra por [`BreakStyle`] + burst + popup + SFX. Devolve `true`
/// quando a entidade despawna (burst/shatter); no Fall a entidade permanece
/// como toco (componentes de colheita/script removidos).
fn break_prop(
    fx: &mut HarvestFx,
    data: &Destructible,
    entity: Entity,
    prop_pos: Vec3,
    player_pos: Vec3,
    terrain: Option<&TerrainRuntime>,
) -> bool {
    let kind = kind_for(data.break_style);
    // Direção da queda/leitura: afastando-se do herói (unitária em XZ).
    let dir = (prop_pos - player_pos).with_y(0.0).normalize_or_zero();
    let dir = if dir.length_squared() < 1e-6 {
        // fallback determinístico por entidade (herói em cima do prop)
        let angle = ((entity.to_bits() % 360) as f32).to_radians();
        Vec3::new(angle.sin(), 0.0, angle.cos())
    } else {
        dir
    };
    // Piso SOB o prop (Y conhecido): a árvore tomba do chão onde está, não
    // do topo do mundo caso esteja sob um overhang.
    let ground_y = terrain
        .map(|t| {
            t.surface_below(
                prop_pos.x,
                prop_pos.z,
                prop_pos.y + crate::player::GROUND_PROBE,
            )
            .unwrap_or_else(|| t.sample(prop_pos.x, prop_pos.z))
        })
        .unwrap_or(prop_pos.y);

    let despawned = match data.break_style {
        BreakStyle::Fall => {
            if !start_tree_fall(fx, entity, dir, ground_y) {
                // Sem `Top` (GLB não pré-dividido): cai para o burst simples.
                fx.commands.entity(entity).despawn();
                true
            } else {
                false
            }
        }
        BreakStyle::Shatter => {
            start_rock_shatter(fx, entity, prop_pos, data, ground_y);
            fx.commands.entity(entity).despawn();
            true
        }
        BreakStyle::Burst => {
            fx.commands.entity(entity).despawn();
            true
        }
    };

    // Burst de break (o "leaves no centro antes da queda" do Fall incluído).
    crate::particles::spawn_burst(
        &mut fx.commands,
        &hit_burst_spec(&data.preset),
        prop_pos + Vec3::Y * 0.8,
        data.burst_count as usize,
    );
    // Popup flutuante (texto/cor autorais via pool do feedback).
    if let Some(popup) = &data.popup {
        fx.numbers.write(DamageNumberEvent {
            position: prop_pos + Vec3::Y * 1.2,
            text: popup.clone(),
            color: data.popup_color,
        });
    }
    fx.sfx.write(SfxEvent {
        clip: sfx_break(kind),
        position: Some(prop_pos),
    });
    despawned
}

// ── FX: queda da árvore ─────────────────────────────────────────────────

/// Geometria da queda: pivô no PLANO DE CORTE (base do `Top` em coords
/// mundo). Usar a ORIGEM do nó como pivô fazia a peça girar em torno do seu
/// centro e ficar "caída no alto" — o corte tem de ser o ponto fixo.
/// Devolve (posição do pivô, translation LOCAL do filho que preserva a pose
/// mundo, comprimento real do topo). `world_min_y`/`world_max_y` = AABB da
/// peça em coords mundo.
fn fall_geometry(
    top_translation: Vec3,
    top_rotation: Quat,
    top_scale: Vec3,
    world_min_y: f32,
    world_max_y: f32,
    ground_y: f32,
) -> (Vec3, Vec3, f32) {
    let cut_y = world_min_y.max(ground_y);
    let pivot = Vec3::new(top_translation.x, cut_y, top_translation.z);
    // Filho do pivô: local = pose_inversa(pivô) ∘ pose_mundo(topo) — rotação
    // relativa é identidade (o pivô arranca com a rotação do topo) e o
    // translation é o offset invertido no espaço do pivô.
    let child_local = top_rotation.inverse() * ((top_translation - pivot) / top_scale);
    let top_length = (world_max_y - world_min_y).max(0.5);
    (pivot, child_local, top_length)
}

/// AABB em Y (coords mundo) de todos os meshes sob `root`. Rotações são
/// ignoradas no eixo Y (árvores só têm yaw — yaw preserva Y).
#[allow(clippy::type_complexity)]
fn mesh_world_y_range(
    root: Entity,
    children: &Query<&Children>,
    mesh_viz: &Query<&Mesh3d>,
    globals: &Query<&GlobalTransform>,
    meshes: &Assets<Mesh>,
) -> Option<(f32, f32)> {
    let mut range: Option<(f32, f32)> = None;
    if let Ok(m3d) = mesh_viz.get(root) {
        if let Some((lo, hi)) = meshes.get(&m3d.0).and_then(mesh_local_y_range) {
            if let Ok(gt) = globals.get(root) {
                let base = gt.translation().y;
                range = Some((base + lo * gt.scale().y, base + hi * gt.scale().y));
            }
        }
    }
    if let Ok(kids) = children.get(root) {
        for child in kids.iter() {
            if let Some((lo, hi)) = mesh_world_y_range(child, children, mesh_viz, globals, meshes) {
                range = Some(match range {
                    Some((a, b)) => (a.min(lo), b.max(hi)),
                    None => (lo, hi),
                });
            }
        }
    }
    range
}

/// min/max Y das posições da malha, em espaço local.
fn mesh_local_y_range(mesh: &Mesh) -> Option<(f32, f32)> {
    let attr = mesh.attribute(Mesh::ATTRIBUTE_POSITION)?;
    let bevy::render::mesh::VertexAttributeValues::Float32x3(values) = attr else {
        return None;
    };
    let (mut min, mut max) = (f32::MAX, f32::MIN);
    for v in values {
        min = min.min(v[1]);
        max = max.max(v[1]);
    }
    (min <= max).then_some((min, max))
}

/// Re-parenta a malha `Top` a um pivô no plano de corte e agenda a queda.
/// `false` (caller cai para o burst) quando o GLB não tem `Top`.
fn start_tree_fall(fx: &mut HarvestFx, entity: Entity, dir: Vec3, ground_y: f32) -> bool {
    let Some(top) = find_child_named(entity, "top", &fx.children, &fx.names) else {
        return false;
    };
    let Ok(top_global) = fx.globals.get(top) else {
        return false;
    };
    // Pivô na BASE da peça (plano de corte, via AABB) — nunca na origem do
    // nó, que fica no meio da malha e deixa o corte suspenso no ar.
    let fallback_top = (top_global.translation().y - ground_y).max(0.5);
    let (world_min_y, world_max_y) =
        mesh_world_y_range(top, &fx.children, &fx.mesh_viz, &fx.globals, &fx.meshes).unwrap_or((
            top_global.translation().y,
            top_global.translation().y + fallback_top,
        ));
    let (pivot_pos, child_local, top_length) = fall_geometry(
        top_global.translation(),
        top_global.rotation(),
        top_global.scale(),
        world_min_y,
        world_max_y,
        ground_y,
    );
    let initial = top_global.rotation();
    let pivot_scale = top_global.scale();

    // Materiais do topo clonados para o fade (AlphaMode::Blend).
    let mut top_materials = Vec::new();
    for (mesh_entity, handle) in collect_materials(top, &fx.children, &fx.mesh_mats) {
        let mut cloned = fx.materials.get(&handle).cloned().unwrap_or_default();
        cloned.alpha_mode = AlphaMode::Blend;
        let cloned_handle = fx.materials.add(cloned);
        fx.commands
            .entity(mesh_entity)
            .insert(MeshMaterial3d(cloned_handle.clone()));
        top_materials.push(cloned_handle);
    }

    let pivot = fx
        .commands
        .spawn((
            Transform {
                translation: pivot_pos,
                rotation: initial,
                scale: pivot_scale,
            },
            Visibility::Inherited,
            TreeFall {
                axis: Vec3::new(dir.z, 0.0, -dir.x).normalize_or_zero(),
                dir,
                initial,
                elapsed: 0.0,
                impact_done: false,
                ground_y,
                cut_point: pivot_pos,
                top_length,
                top_materials,
            },
            Name::new("fx:tree-fall"),
        ))
        .id();
    // Top → filho do pivô com o offset que preserva a pose mundo (a origem
    // do nó pode estar longe do corte — o pivô é que rota à volta da base).
    fx.commands.entity(top).insert((
        Transform {
            translation: child_local,
            ..Transform::IDENTITY
        },
        ChildOf(pivot),
    ));
    // O toco persiste SEM colheita nem script — o collider cápsula do tronco
    // fica, como o `spawnPersistentStump` da referência.
    fx.commands.entity(entity).remove::<Destructible>();
    fx.commands
        .entity(entity)
        .remove::<crate::luau::LuaScriptRef>();
    fx.commands.entity(entity).remove::<ScriptInteraction>();
    true
}

/// Conduz a queda: ease-in `t^2.4` até [`FALL_MAX_ANGLE`], bursts no
/// impacto, hold, fade do topo, despawn do pivô (com o topo).
#[allow(clippy::type_complexity)]
pub fn tree_fall_system(
    time: Res<Time>,
    mut falls: Query<(Entity, &mut Transform, &mut TreeFall)>,
    mut materials: ResMut<Assets<StandardMaterial>>,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    for (entity, mut transform, mut fall) in &mut falls {
        fall.elapsed += dt;
        let t = fall.elapsed;
        if t < FALL_DURATION {
            let angle = fall_angle_rad(t, FALL_DURATION);
            transform.rotation = Quat::from_axis_angle(fall.axis, angle) * fall.initial;
        } else if !fall.impact_done {
            fall.impact_done = true;
            transform.rotation = Quat::from_axis_angle(fall.axis, FALL_MAX_ANGLE) * fall.initial;
            // Bursts de impacto no ponto de queda (≈ pivô + dir·len·0.75).
            let point = fall.cut_point + fall.dir * (fall.top_length * FALL_IMPACT_FRACTION);
            crate::particles::spawn_burst(
                &mut commands,
                &hit_burst_spec("ground-dust"),
                Vec3::new(point.x, fall.ground_y + 0.15, point.z),
                30,
            );
            crate::particles::spawn_burst(
                &mut commands,
                &hit_burst_spec("leaves"),
                Vec3::new(point.x, fall.ground_y + 0.5, point.z),
                25,
            );
        }
        let fade_start = FALL_DURATION + FALL_IMPACT_HOLD;
        if t > fade_start {
            let k = (t - fade_start) / FALL_FADE;
            if k >= 1.0 {
                commands.entity(entity).despawn();
                continue;
            }
            set_materials_alpha(
                &mut materials,
                &fall.top_materials,
                (1.0 - k).clamp(0.0, 1.0),
            );
        }
    }
}

// ── FX: estilhaços da rocha ─────────────────────────────────────────────

/// 9 pedaços icosaédricos flat-shaded com balística própria; cor = cinza
/// derivado do popup_color ×0.85 (desvio documentado no topo do módulo).
fn start_rock_shatter(
    fx: &mut HarvestFx,
    entity: Entity,
    prop_pos: Vec3,
    data: &Destructible,
    ground_y: f32,
) {
    let mut rng = Rng::new(entity.to_bits() ^ 0x5EED_D1CE);
    let prop_scale = prop_scale_hint(fx, entity);
    let material = fx.materials.add(StandardMaterial {
        base_color: darken_color(data.popup_color, 0.85),
        perceptual_roughness: 0.95,
        metallic: 0.0,
        alpha_mode: AlphaMode::Blend,
        ..StandardMaterial::default()
    });
    let mesh = fx.meshes.add(icosahedron_mesh());

    for _ in 0..SHATTER_PIECES {
        let piece_scale = debris_scale(&mut rng, prop_scale);
        let position = Vec3::new(
            prop_pos.x + (rng.next_f32() - 0.5) * 0.5 * prop_scale,
            ground_y + 0.4 + rng.next_f32() * 0.6 * prop_scale,
            prop_pos.z + (rng.next_f32() - 0.5) * 0.5 * prop_scale,
        );
        let rotation = Quat::from_euler(
            EulerRot::XYZ,
            rng.next_f32() * std::f32::consts::PI,
            rng.next_f32() * std::f32::consts::PI,
            rng.next_f32() * std::f32::consts::PI,
        );
        fx.commands.spawn((
            Transform {
                translation: position,
                rotation,
                scale: piece_scale,
            },
            Visibility::Inherited,
            Mesh3d(mesh.clone()),
            MeshMaterial3d(material.clone()),
            DebrisChunk {
                vel: debris_velocity(&mut rng),
                angvel: debris_angvel(&mut rng),
                rest_half: piece_scale.y * 0.5,
                ground_hint: ground_y,
                landed: false,
                elapsed: 0.0,
                material: material.clone(),
            },
            Name::new("fx:debris"),
        ));
    }
}

/// Escala do prop (para os pedaços seguirem o tamanho do original).
fn prop_scale_hint(fx: &HarvestFx, entity: Entity) -> f32 {
    fx.globals
        .get(entity)
        .map(|g| g.scale().max_element().max(0.4))
        .unwrap_or(1.0)
}

/// Icosaedro (subdivisões 0) flat-shaded — o `IcosahedronGeometry(1, 0)` da
/// referência. A malha sai INDEXADA e `compute_flat_normals` panica em
/// geometria indexada (bevy_mesh 0.19) — duplicar vértices primeiro.
fn icosahedron_mesh() -> Mesh {
    let mut mesh = Sphere::new(1.0)
        .mesh()
        .ico(0)
        .unwrap_or_else(|_| Mesh::from(Sphere::new(1.0)));
    mesh.duplicate_vertices();
    mesh.compute_flat_normals();
    mesh
}

/// Balística dos pedaços: gravidade 20, tumble, pousam em
/// terrain_y+escala/2, hold [`SHATTER_HOLD`], fade [`SHATTER_FADE`],
/// despawn.
#[allow(clippy::type_complexity)]
pub fn debris_chunk_system(
    time: Res<Time>,
    terrain: Option<Res<TerrainRuntime>>,
    mut pieces: Query<(Entity, &mut Transform, &mut DebrisChunk)>,
    mut materials: ResMut<Assets<StandardMaterial>>,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    for (entity, mut transform, mut piece) in &mut pieces {
        piece.elapsed += dt;
        if !piece.landed {
            piece.vel.y -= SHATTER_GRAVITY * dt;
            transform.translation += piece.vel * dt;
            let spin = piece.angvel * dt;
            transform.rotation =
                Quat::from_euler(EulerRot::XYZ, spin.x, spin.y, spin.z) * transform.rotation;
            let ground = terrain
                .as_deref()
                .and_then(|t| {
                    t.surface_below(
                        transform.translation.x,
                        transform.translation.z,
                        transform.translation.y + crate::player::GROUND_PROBE,
                    )
                })
                .unwrap_or(piece.ground_hint);
            let rest_y = ground + piece.rest_half;
            if piece.vel.y < 0.0 && transform.translation.y <= rest_y {
                transform.translation.y = rest_y;
                piece.landed = true;
            }
        }
        if piece.elapsed > SHATTER_HOLD {
            let k = (piece.elapsed - SHATTER_HOLD) / SHATTER_FADE;
            if k >= 1.0 {
                commands.entity(entity).despawn();
                continue;
            }
            set_materials_alpha(
                &mut materials,
                std::slice::from_ref(&piece.material),
                (1.0 - k).clamp(0.0, 1.0),
            );
        }
    }
}

// ── FX: wobble por golpe ────────────────────────────────────────────────

/// Constrói o [`HitShake`] sobre os filhos visuais do prop (fallback: o
/// próprio prop). `None` sem transform possível.
fn build_hit_shake(fx: &HarvestFx, prop: Entity) -> Option<HitShake> {
    let mut targets = Vec::new();
    if let Ok(children) = fx.children.get(prop) {
        for child in children.iter() {
            if let Ok(transform) = fx.transforms.get(child) {
                targets.push((child, transform.rotation));
            }
        }
    }
    if targets.is_empty() {
        if let Ok(transform) = fx.transforms.get(prop) {
            targets.push((prop, transform.rotation));
        }
    }
    (!targets.is_empty()).then_some(HitShake {
        elapsed: 0.0,
        targets,
    })
}

/// Wobble rotacional decrescente (rotation local x/z dos filhos), restaura
/// as rotações originais no fim.
#[allow(clippy::type_complexity)]
pub fn hit_shake_system(
    time: Res<Time>,
    mut shakes: Query<(Entity, &mut HitShake)>,
    mut transforms: Query<&mut Transform, Without<crate::player::Player>>,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    for (entity, mut shake) in &mut shakes {
        shake.elapsed += dt;
        let k = shake.elapsed / SHAKE_DURATION;
        if k >= 1.0 {
            for (child, base) in &shake.targets {
                if let Ok(mut transform) = transforms.get_mut(*child) {
                    transform.rotation = *base;
                }
            }
            commands.entity(entity).remove::<HitShake>();
            continue;
        }
        let decay = (1.0 - k) * SHAKE_AMP;
        let phase = shake.elapsed * SHAKE_PHASE_HZ;
        for (child, base) in &shake.targets {
            if let Ok(mut transform) = transforms.get_mut(*child) {
                let wobble = Quat::from_euler(
                    EulerRot::XYZ,
                    phase.sin() * decay,
                    0.0,
                    (phase * 1.3).cos() * decay,
                );
                transform.rotation = *base * wobble;
            }
        }
    }
}

// ── helpers de materiais ────────────────────────────────────────────────

/// Alfa dos materiais (fade do topo/pedaços) — base_color com alpha animado.
fn set_materials_alpha(
    materials: &mut Assets<StandardMaterial>,
    handles: &[Handle<StandardMaterial>],
    alpha: f32,
) {
    for handle in handles {
        if let Some(mut material) = materials.get_mut(handle) {
            material.base_color = material.base_color.with_alpha(alpha);
        }
    }
}

/// Aplica/estende o escurecimento: clona os materiais do prop UMA vez
/// (clones no componente [`Darken`]) e escreve `base × 0.85^hits` sobre
/// eles — sem acumular erro de multiplicação nos clones.
fn apply_darken(fx: &mut HarvestFx, darkens: &Query<&Darken>, entity: Entity, hits_taken: u32) {
    let factor = darken_factor(hits_taken);
    if let Ok(darken) = darkens.get(entity) {
        // Golpes seguintes: repinta os clones a partir da base guardada.
        for handle in &darken.clones {
            if let Some(mut material) = fx.materials.get_mut(handle) {
                material.base_color = darken_color(darken.base, factor);
            }
        }
        return;
    }
    // Primeiro golpe: clona os materiais visíveis do prop e troca-os nas
    // malhas (os outros props que partilham o material ficam intactos).
    let originals = collect_materials(entity, &fx.children, &fx.mesh_mats);
    if originals.is_empty() {
        return;
    }
    let base = originals
        .first()
        .and_then(|(_, handle)| fx.materials.get(handle))
        .map(|m| m.base_color)
        .unwrap_or(Color::WHITE);
    let mut clones = Vec::with_capacity(originals.len());
    for (mesh_entity, handle) in originals {
        let cloned = fx.materials.get(&handle).cloned().unwrap_or_default();
        let clone_handle = fx.materials.add(cloned);
        fx.commands
            .entity(mesh_entity)
            .insert(MeshMaterial3d(clone_handle.clone()));
        clones.push(clone_handle);
    }
    for handle in &clones {
        if let Some(mut material) = fx.materials.get_mut(handle) {
            material.base_color = darken_color(base, factor);
        }
    }
    fx.commands.entity(entity).insert(Darken { base, clones });
}

/// Procura um descendente por `Name` (case-insensitive) — o `Top` dos GLBs
/// pré-divididos. A RAIZ não tem `Name` (instâncias de spawner são anónimas):
/// a ausência de name nela não pode abortar a busca aos filhos.
fn find_child_named(
    root: Entity,
    name: &str,
    children: &Query<&Children>,
    names: &Query<&Name>,
) -> Option<Entity> {
    if let Ok(own) = names.get(root) {
        if own.as_str().to_ascii_lowercase() == name {
            return Some(root);
        }
    }
    for child in children.get(root).ok()?.iter() {
        if let Some(found) = find_child_named(child, name, children, names) {
            return Some(found);
        }
    }
    None
}

/// Recolhe (entidade, handle) de materiais na subárvore (inclui a raiz).
fn collect_materials(
    root: Entity,
    children: &Query<&Children>,
    mesh_mats: &Query<&MeshMaterial3d<StandardMaterial>>,
) -> Vec<(Entity, Handle<StandardMaterial>)> {
    let mut out = Vec::new();
    collect_materials_walk(root, children, mesh_mats, &mut out);
    out
}

fn collect_materials_walk(
    entity: Entity,
    children: &Query<&Children>,
    mesh_mats: &Query<&MeshMaterial3d<StandardMaterial>>,
    out: &mut Vec<(Entity, Handle<StandardMaterial>)>,
) {
    if let Ok(mat) = mesh_mats.get(entity) {
        out.push((entity, mat.0.clone()));
    }
    if let Ok(kids) = children.get(entity) {
        for child in kids.iter() {
            collect_materials_walk(child, children, mesh_mats, out);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_icosahedron_mesh_is_flat_shaded_safe() {
        // regressão: `compute_flat_normals` panica em geometria indexada —
        // o helper tem de entregar malha NÃO-indexada (flat-shaded real).
        let mesh = icosahedron_mesh();
        assert!(mesh.indices().is_none(), "vértices duplicados: sem índices");
        assert!(mesh.attribute(Mesh::ATTRIBUTE_NORMAL).is_some());
    }

    #[test]
    fn test_fall_geometry_pivot_at_cut_plane() {
        // regressão: pivô na ORIGEM do nó `Top` (meio da malha) deixava a
        // árvore caída no alto — o pivô é a base da peça (plano de corte).
        let yaw = Quat::from_rotation_y(std::f32::consts::FRAC_PI_6);
        let scale = Vec3::new(1.2, 2.0, 1.2);
        // nó do topo em (10, 7, 0); malha local de y −1 a +5 (× escala 2 →
        // mundo y 5 a 17); solo em y 4.
        let (pivot, child_local, length) = fall_geometry(
            Vec3::new(10.0, 7.0, 0.0),
            yaw,
            scale,
            7.0 + (-1.0) * 2.0,
            7.0 + 5.0 * 2.0,
            4.0,
        );
        // pivô na base da peça (plano de corte), XZ intactos
        assert_eq!(pivot, Vec3::new(10.0, 5.0, 0.0));
        // comprimento real da peça (12 m, não a altura do corte)
        assert!((length - 12.0).abs() < 1e-4);
        // o filho preserva a pose mundo: recomposição pivô∘local == original
        let recomposed = pivot + yaw * (child_local * scale);
        assert!((recomposed - Vec3::new(10.0, 7.0, 0.0)).length() < 1e-4);
    }

    #[test]
    fn test_fall_geometry_clamps_pivot_to_ground() {
        let (pivot, _, _) = fall_geometry(
            Vec3::new(0.0, 3.0, 0.0),
            Quat::IDENTITY,
            Vec3::ONE,
            -2.0, // base da peça abaixo do solo
            9.0,
            0.0,
        );
        assert_eq!(pivot.y, 0.0, "plano de corte nunca abaixo do terreno");
    }

    #[test]
    fn test_find_child_named_with_unnamed_root() {
        // regressão: instâncias de spawner NÃO têm Name — a busca pelo `Top`
        // tem de atravessar a raiz anónima (sem isto, toda a queda de árvore
        // caía no fallback burst e despawnava o tronco).
        use bevy::ecs::system::RunSystemOnce;

        #[derive(Resource)]
        struct Alvo(Entity);

        let mut world = World::new();
        let root = world.spawn_empty().id();
        let scene = world.spawn((Name::new("Scene"), ChildOf(root))).id();
        let stump = world.spawn((Name::new("Stump"), ChildOf(scene))).id();
        let top = world.spawn((Name::new("Top"), ChildOf(scene))).id();
        world.insert_resource(Alvo(root));

        let found = world
            .run_system_once(
                |alvo: Res<Alvo>, children: Query<&Children>, names: Query<&Name>| {
                    find_child_named(alvo.0, "top", &children, &names)
                },
            )
            .unwrap();
        assert_eq!(found, Some(top));
        assert_ne!(found, Some(stump));
    }

    #[test]
    fn test_map_preset_translates_reference_names() {
        assert_eq!(map_preset("rockshards"), "sparks");
        assert_eq!(map_preset("woodchips"), "leaves");
        assert_eq!(map_preset("dust"), "ground-dust");
        // presets já nativos passam intactos; desconhecidos idem (core no
        // runtime, warning nenhum)
        assert_eq!(map_preset("leaves"), "leaves");
        assert_eq!(map_preset("ground-dust"), "ground-dust");
        assert_eq!(map_preset("mystery"), "mystery");
    }

    #[test]
    fn test_from_spec_resolves_reference_defaults() {
        // spec vazio → defaults do plugin destructible
        let d = Destructible::from_spec(&DestructibleSpec::parse(""));
        assert_eq!(d.hits, 3);
        assert_eq!(d.hits_taken, 0);
        assert!((d.range - 3.5).abs() < 1e-5);
        assert_eq!(d.break_style, BreakStyle::Burst);
        assert_eq!(d.preset, "ground-dust", "preset default dust→ground-dust");
        assert_eq!(d.burst_count, 60);
        assert_eq!(d.hit_preset, "sparks");
        assert_eq!(d.hit_burst_count, 15);
        assert!(!d.shake_on_hit);
        assert!(d.popup.is_none());
        assert!(d.resource.is_none());
        // hits 0 / range 0 do XML nunca deixam o prop inquebrável
        let zero = Destructible::from_spec(&DestructibleSpec::parse("hits: 0; range: 0"));
        assert_eq!(zero.hits, 1);
        assert!((zero.range - 3.5).abs() < 1e-5);
    }

    #[test]
    fn test_from_spec_keeps_authored_values() {
        let d = Destructible::from_spec(&DestructibleSpec::parse(
            "popup-text: Stone; popup-color: #cccccc; preset: dust; burst-count: 22; \
             hits: 5; hit-preset: rockshards; hit-burst-count: 14; shake-on-hit: 1; \
             break-style: shatter",
        ));
        assert_eq!(d.hits, 5);
        assert_eq!(d.burst_count, 22);
        assert_eq!(d.hit_preset, "sparks", "rockshards→sparks");
        assert_eq!(d.hit_burst_count, 14);
        assert!(d.shake_on_hit);
        assert_eq!(d.break_style, BreakStyle::Shatter);
        assert_eq!(d.popup.as_deref(), Some("Stone"));
        let srgba = d.popup_color.to_srgba();
        assert!((srgba.red - 0.8).abs() < 1e-4, "cccc/255 ≈ 0.8");
        assert!((srgba.green - 0.8).abs() < 1e-4);
        assert!((srgba.blue - 0.8).abs() < 1e-4);
        // resource injetado pelo parse do spawner chega ao componente
        let mut spec = DestructibleSpec::parse("break-style: fall");
        spec.resource = Some(("wood".to_string(), 3));
        let tree = Destructible::from_spec(&spec);
        assert_eq!(tree.resource, Some(("wood".to_string(), 3)));
        assert_eq!(tree.break_style, BreakStyle::Fall);
    }

    #[test]
    fn test_kind_prompt_clip_and_sfx_pairing() {
        assert_eq!(kind_for(BreakStyle::Fall), HarvestKind::Chop);
        assert_eq!(kind_for(BreakStyle::Shatter), HarvestKind::Mine);
        assert_eq!(kind_for(BreakStyle::Burst), HarvestKind::Mine);
        assert_eq!(prompt_label(HarvestKind::Mine), "Minerar");
        assert_eq!(prompt_label(HarvestKind::Chop), "Cortar");
        assert_eq!(clip_for(HarvestKind::Mine), "mine");
        assert_eq!(clip_for(HarvestKind::Chop), "chop");
        assert_eq!(sfx_hit(HarvestKind::Mine), SfxClip::MineHit);
        assert_eq!(sfx_hit(HarvestKind::Chop), SfxClip::ChopHit);
        assert_eq!(sfx_break(HarvestKind::Mine), SfxClip::MineBreak);
        assert_eq!(sfx_break(HarvestKind::Chop), SfxClip::ChopBreak);
    }

    #[test]
    fn test_impact_delay_matches_melee_fraction() {
        // clip de ~1.5 s a 1.4× → 1.5·0.35/1.4 ≈ 0.375 s
        let delay = impact_delay(1.5, ATTACK_TIME_SCALE);
        assert!((delay - 0.375).abs() < 1e-3, "{delay}");
        // clamps de segurança
        assert_eq!(impact_delay(0.2, 1.0), IMPACT_DELAY_MIN);
        assert_eq!(impact_delay(5.0, 1.0), IMPACT_DELAY_MAX);
        // sem duração conhecida → fallback
        assert_eq!(impact_delay(0.0, ATTACK_TIME_SCALE), FALLBACK_IMPACT_DELAY);
        assert_eq!(impact_delay(1.5, 0.0), FALLBACK_IMPACT_DELAY);
    }

    #[test]
    fn test_fall_angle_eases_in_to_84_6_deg() {
        assert!(fall_angle_rad(0.0, FALL_DURATION).abs() < 1e-6);
        let mid = fall_angle_rad(0.5, FALL_DURATION);
        assert!(mid < FALL_MAX_ANGLE * 0.5, "ease-in: {mid}");
        assert!(
            (fall_angle_rad(FALL_DURATION, FALL_DURATION) - FALL_MAX_ANGLE).abs() < 1e-5,
            "ângulo final ≈ 84.6°"
        );
        assert!(
            (FALL_MAX_ANGLE.to_degrees() - 84.6).abs() < 0.1,
            "{}",
            FALL_MAX_ANGLE.to_degrees()
        );
        // para além do fim: clampa no ângulo máximo
        assert_eq!(fall_angle_rad(9.0, FALL_DURATION), FALL_MAX_ANGLE);
        // monotonicidade (a queda acelera)
        let mut prev = 0.0;
        for step in 1..=10 {
            let angle = fall_angle_rad(step as f32 / 10.0, FALL_DURATION);
            assert!(angle > prev, "t={step}");
            prev = angle;
        }
    }

    #[test]
    fn test_debris_bounds_match_reference() {
        let mut rng = Rng::new(0xD3B0);
        for _ in 0..200 {
            let vel = debris_velocity(&mut rng);
            let horizontal = Vec3::new(vel.x, 0.0, vel.z).length();
            assert!((1.5..=5.0).contains(&horizontal), "h={horizontal}");
            assert!((2.5..=6.0).contains(&vel.y), "vy={}", vel.y);
            let ang = debris_angvel(&mut rng);
            assert!(ang.x.abs() <= 5.0 + 1e-4);
            assert!(ang.y.abs() <= 5.0 + 1e-4);
            assert!(ang.z.abs() <= 5.0 + 1e-4);
            // escala: 0.1–0.26 × max(scale, 0.4), eixos 0.6–1.4
            let s = debris_scale(&mut rng, 1.0);
            for v in s.to_array() {
                assert!((0.1 * 0.6..=0.26 * 1.4).contains(&v), "s={v}");
            }
        }
        // prop pequeno: escala capada a 0.4
        let small = debris_scale(&mut Rng::new(7), 0.2);
        let max = small.max_element();
        assert!(max <= 0.26 * 1.4 * 0.4 + 1e-4, "cap 0.4: {max}");
    }

    #[test]
    fn test_darken_color_and_factor() {
        let white = Color::srgb(1.0, 1.0, 1.0);
        let once = darken_color(white, 0.85);
        assert!((once.to_srgba().red - 0.85).abs() < 1e-5);
        // fator é potência de 0.85 por golpe (cap a 8)
        assert!((darken_factor(0) - 1.0).abs() < 1e-6);
        assert!((darken_factor(1) - 0.85).abs() < 1e-6);
        assert!((darken_factor(2) - 0.85 * 0.85).abs() < 1e-6);
        assert!((darken_factor(99) - darken_factor(8)).abs() < 1e-6, "cap");
        // alpha preservado
        let ghost = Color::Srgba(bevy::color::Srgba::new(1.0, 1.0, 1.0, 0.5));
        assert!((darken_color(ghost, 0.5).to_srgba().alpha - 0.5).abs() < 1e-6);
    }

    #[test]
    fn test_grant_loot_follows_report_collect_path() {
        let mut vault = Vault::default();
        grant_loot(&mut vault, Some(&("wood".to_string(), 3)));
        assert_eq!(vault.wood, 3);
        grant_loot(&mut vault, Some(&("stone".to_string(), 4)));
        assert_eq!(vault.stone, 4);
        // item de objetivo (não é recurso) → item_add, como no report_collect
        grant_loot(&mut vault, Some(&("dark-wood".to_string(), 2)));
        assert_eq!(vault.item_count("dark-wood"), 2);
        // sem resource: nada (só XP)
        grant_loot(&mut vault, None);
        assert_eq!(vault.gold, 0);
    }
}
