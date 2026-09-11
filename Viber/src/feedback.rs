//! Feedback de combate (loop 2 do port simple-rpg) — o análogo nativo do
//! `CombatFeedbackSystem`/`RespawnSystem`/hurt-vignette do VibeGame:
//!
//! - **Dano flutuante**: pool de textos UI world-anchored (projecção da
//!   câmara) que sobem e desvanecem — `-25` no alvo, dourado para XP — com
//!   pop de escala com overshoot nos primeiros ~0.1 s.
//! - **Hurt vignettes + i-frames**: todo dano ao herói passa por
//!   [`hurt_player`] (via `PlayerHurt`), respeita `Invulnerable` (0,35 s),
//!   acende a vinheta vermelha de dano e não acerta durante `Dying`. Com HP
//!   < 30 %, uma segunda vinheta PERSISTENTE pulsa (1,1 Hz) até curar.
//! - **Alvo de combate**: o melee do herói fixa o alvo ([`CombatTarget`],
//!   TTL 8 s); um anel vermelho pulsante marca os pés do alvo e as barras de
//!   HP vivem na UI declarativa (`src/ui/`, bindings `viber.*`).
//! - **RespawnSystem**: HP 0 → `Dying` 2 s → volta ao ponto mais próximo
//!   (praça/portões) com HP cheio e i-frames.
//! - **Status effects mínimos**: veneno tickado por segundo; scripts Luau
//!   aplicam com `viber.apply_status("venom", secs)`.
//!
//! Camera shake fica para o dono da câmara (cross-scope).

use bevy::camera::Camera3d;
use bevy::light::NotShadowCaster;
use bevy::prelude::*;

use crate::luau::ScriptToast;
use crate::player::Player;
use crate::skills::{GUARD_REDUCTION, Guarding};
use crate::vitals::{Health, apply_damage};

/// Janela de invulnerabilidade após cada golpe sofrido (s) — VibeGame 0,35.
pub const IFRAME_SECS: f32 = 0.35;
/// Espera entre a morte e o respawn (s) — VibeGame RespawnSystem.
pub const RESPAWN_DELAY: f32 = 2.0;
/// Segundos sem combate até o alvo da TargetBar se perder.
pub const TARGET_TTL: f32 = 8.0;
/// Dano por segundo do veneno (tick 1/s).
pub const VENOM_DPS: f32 = 4.0;
/// Vida útil de um número de dano (s).
const NUMBER_LIFETIME: f32 = 0.9;
/// Tamanho do pool de números de dano (slots reutilizados).
const NUMBER_POOL: usize = 14;
/// Vida do flash branco-quente no inimigo atingido (s) — o "hit flash" que
/// faz o impacto LER a distância. R8: 0.45 s — o BOTW mantém o flash ~0.3-0.5;
/// curto demais e o momento perde-se em slow-mo/capturas.
pub const HIT_FLASH_SECS: f32 = 0.45;
/// Pico do emissive do flash — R4: 4.5 (8/14 queimavam a criatura pequena a
/// silhueta chapada no tonemap do anoitecer); quente, com forma sempre visível.
const HIT_FLASH_EMISSIVE: f32 = 4.5;
/// Teto de materiais clonados por flash (GLBs de personagem têm < 20).
/// `pub(crate)`: o fade de corpses (`combat.rs`) partilha o mesmo cap.
pub(crate) const MAX_FLASH_MATS: usize = 24;

// ── eventos ─────────────────────────────────────────────────────────────

/// Dano ao herói — ÚNICO caminho do dano (scripts `damage_player`, veneno).
/// A aplicação real acontece em [`player_hurt_system`] com i-frames/vinheta.
#[derive(Debug, Clone, bevy::ecs::message::Message)]
pub struct PlayerHurt {
    pub amount: f32,
    /// `true` para dano de status (veneno): ignora i-frames, sem número.
    pub status: bool,
    /// Posição do atacante (knockback no herói); `None` = sem origem.
    pub from: Option<Vec3>,
}

/// Número flutuante a mostrar no mundo (consumido pelo pool de UI).
#[derive(Debug, Clone, bevy::ecs::message::Message)]
pub struct DamageNumberEvent {
    pub position: Vec3,
    pub text: String,
    pub color: Color,
}

/// Aggro-chain (loop 6): o herói acertou uma criatura nesta posição —
/// aliados num raio de 15 m recebem `on_player_attack(px, pz)` nos scripts.
#[derive(Debug, Clone, Copy, bevy::ecs::message::Message)]
pub struct AttackAlert {
    pub position: Vec3,
}

// ── componentes / recursos ──────────────────────────────────────────────

/// Janela de i-frames do herói (decrementada por frame).
#[derive(Debug, Clone, Component)]
pub struct Invulnerable {
    pub timer: f32,
}

/// Herói morto: espera [`RESPAWN_DELAY`] e renasce no ponto mais próximo.
#[derive(Debug, Clone, Component)]
pub struct Dying {
    pub timer: f32,
}

/// Status effects activos no herói (mínimo: veneno).
#[derive(Debug, Clone, Component, Default)]
pub struct StatusEffects {
    /// Segundos restantes de veneno (0 = sem veneno).
    pub venom: f32,
    /// Acumulador para o tick de 1 s (recomeça em cada aplicação).
    pub venom_tick: f32,
}

/// Alvo de combate actual do herói (soft-lock do VibeGame: fixa ao acertar).
#[derive(Debug, Clone, Resource)]
pub struct CombatTarget {
    pub entity: Option<Entity>,
    pub timer: f32,
}

impl Default for CombatTarget {
    fn default() -> Self {
        Self {
            entity: None,
            timer: 0.0,
        }
    }
}

/// Intensidade actual da vinheta de dano (0..1), decai exponencialmente.
#[derive(Debug, Clone, Resource, Default)]
pub struct HurtFlash(pub f32);

/// Slot do pool de números de dano.
#[derive(Debug, Clone, Component)]
struct DamageNumberSlot {
    world_pos: Vec3,
    age: f32,
    active: bool,
    /// Tamanho de fonte atribuído à ativação ([`number_font_size`]) — a base
    /// do pop de escala ([`damage_number_scale`]).
    base_font_size: f32,
}

/// Flash de impacto no inimigo: enquanto `timer > 0`, a subárvore do modelo
/// acende em branco-quente (`HIT_FLASH_EMISSIVE` no pico, queda quadrática).
/// Inserido pelas vias de dano do combate (melee, fireball).
#[derive(Debug, Clone, Component)]
pub struct HitFlash {
    pub timer: f32,
}

/// Materiais CLONADOS que esta entidade possui durante o flash. Os GLBs das
/// criaturas partilham materiais entre instâncias do mesmo spawner — mutar o
/// asset original acenderia TODOS os lobos quando um apanha. O clone é
/// trocado no 1.º frame do flash e fica (visualmente idêntico, emissive
/// apagado no fim) — sem reversão de handles.
#[derive(Component)]
/// Materiais clonados para o hit-flash de UM inimigo: `(nó, original, clone)`.
/// O clone é necessário (os assets são partilhados entre instâncias — mutar o
/// original acenderia TODOS os lobos), mas o par `(nó, original)` existe para
/// o fim do flash REVERTER a troca e REMOVER o clone do asset store: o Bevy
/// não faz GC de assets, portanto deixá-los ficar (o comportamento anterior)
/// era um leak de até [`MAX_FLASH_MATS`] materiais POR GOLPE, para sempre.
struct FlashMaterials(Vec<(Entity, Handle<StandardMaterial>, Handle<StandardMaterial>)>);

/// Intensidade do flash no instante com `timer` restante — queda quadrática:
/// pop imediato, desvanecimento rápido (o golpe tem de LER no frame do toque).
pub fn hit_flash_intensity(timer: f32) -> f32 {
    let t = (timer / HIT_FLASH_SECS).clamp(0.0, 1.0);
    t * t
}

/// Tamanho da fonte de um número de dano — críticos e remates dominam (o
/// `-50 CRIT!` do BOTW é o dobro do hit normal). R2: escala de CINEMA — o
/// número é parte da composição, não uma anotação.
pub fn number_font_size(text: &str) -> f32 {
    if text.contains("CRIT") {
        46.0
    } else if text.contains('!') || text.contains("x2") {
        38.0
    } else {
        30.0
    }
}

// ── pop de escala dos números de dano ───────────────────────────────────

/// Overshoot do pop de escala (1.25× do tamanho base).
pub const NUMBER_POP_PEAK: f32 = 1.25;
/// Idade (s) em que o pop atinge o pico do overshoot.
pub const NUMBER_POP_PEAK_AT: f32 = 0.04;
/// Idade (s) em que o pop assenta em 1.0.
pub const NUMBER_POP_SETTLE_AT: f32 = 0.10;

/// smootherstep (quíntica, C²): as duas fases do pop não têm vincos.
fn smootherstep(t: f32) -> f32 {
    let t = t.clamp(0.0, 1.0);
    t * t * t * (t * (t * 6.0 - 15.0) + 10.0)
}

/// Pop de escala de um número de dano com `age` segundos: sobe suave até
/// [`NUMBER_POP_PEAK`]× aos [`NUMBER_POP_PEAK_AT`] s e assenta em 1.0 aos
/// [`NUMBER_POP_SETTLE_AT`] s; depois disso é 1.0 (o fade é da alpha, já
/// existente). Puro para testes.
pub fn damage_number_scale(age: f32) -> f32 {
    if age <= 0.0 {
        return 1.0;
    }
    if age <= NUMBER_POP_PEAK_AT {
        let t = age / NUMBER_POP_PEAK_AT;
        return 1.0 + (NUMBER_POP_PEAK - 1.0) * smootherstep(t);
    }
    if age <= NUMBER_POP_SETTLE_AT {
        let t = (age - NUMBER_POP_PEAK_AT) / (NUMBER_POP_SETTLE_AT - NUMBER_POP_PEAK_AT);
        return NUMBER_POP_PEAK - (NUMBER_POP_PEAK - 1.0) * smootherstep(t);
    }
    1.0
}

// ── lógica pura (testada) ───────────────────────────────────────────────

/// Pontos de respawn: praça + 4 portões cardeais (LOOKOUT_GATES do VibeGame).
pub const RESPAWN_POINTS: [Vec2; 5] = [
    Vec2::ZERO,
    Vec2::new(0.0, -50.0),
    Vec2::new(0.0, 50.0),
    Vec2::new(-50.0, 0.0),
    Vec2::new(50.0, 0.0),
];

/// Ponto de respawn mais próximo da posição de morte (XZ).
pub fn nearest_respawn_point(from: Vec2) -> Vec2 {
    RESPAWN_POINTS
        .iter()
        .copied()
        .min_by(|a, b| {
            a.distance_squared(from)
                .total_cmp(&b.distance_squared(from))
        })
        .unwrap_or(Vec2::ZERO)
}

/// Rótulo humano do ponto de respawn (toast de retorno).
pub fn respawn_label(point: Vec2) -> &'static str {
    match point {
        Vec2::ZERO => "praça",
        p if p.y < 0.0 => "portão sul",
        p if p.y > 0.0 => "portão norte",
        p if p.x < 0.0 => "portão oeste",
        _ => "portão leste",
    }
}

/// Resultado de tentar ferir o herói.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum HurtOutcome {
    /// Herói morto ou em `Dying`: dano ignorado.
    Ignored,
    /// Bloqueado por i-frames (dano físico apenas).
    Blocked,
    /// Aplicado; `killed` = HP chegou a 0.
    Applied { killed: bool },
}

/// Caminho único de dano ao herói: i-frames (físico, só com timer > 0 —
/// o componente fica, o QUE importa é a janela), `Dying` ignora tudo,
/// clamp no pool e deteção de morte.
pub fn hurt_player(
    health: &mut Health,
    invuln: Option<&Invulnerable>,
    dying: Option<&Dying>,
    amount: f32,
    status: bool,
) -> HurtOutcome {
    if dying.is_some() {
        return HurtOutcome::Ignored;
    }
    if !status && invuln.is_some_and(|frame| frame.timer > 0.0) {
        return HurtOutcome::Blocked;
    }
    apply_damage(health, amount);
    HurtOutcome::Applied {
        killed: health.current <= 0.0,
    }
}

/// Avança o veneno: devolve dano do tick (1/s) ou 0.
pub fn tick_venom(effects: &mut StatusEffects, dt: f32) -> f32 {
    if effects.venom <= 0.0 {
        return 0.0;
    }
    effects.venom = (effects.venom - dt).max(0.0);
    effects.venom_tick += dt;
    if effects.venom_tick >= 1.0 {
        effects.venom_tick %= 1.0;
        VENOM_DPS
    } else {
        0.0
    }
}

// ── plugin ──────────────────────────────────────────────────────────────

pub struct FeedbackPlugin;

impl Plugin for FeedbackPlugin {
    fn build(&self, app: &mut App) {
        // auto-suficiente em apps mínimas (registos idempotentes)
        app.add_message::<ScriptToast>()
            .add_message::<PlayerHurt>()
            .add_message::<DamageNumberEvent>()
            .add_message::<AttackAlert>()
            // hurt/death/block do herói viajam em SfxEvent (o AmbientPlugin
            // também regista; apps mínimas podem não o ter).
            .add_message::<crate::ambient::SfxEvent>()
            .init_resource::<CombatTarget>()
            .init_resource::<HurtFlash>()
            // O hit-flash clona StandardMaterials — apps mínimas precisam do
            // registo (bare `Assets`, sem AssetServer; na app completa o
            // AssetPlugin já o inseriu e isto é no-op).
            .init_resource::<Assets<StandardMaterial>>()
            // Meshes para o anel de alvo (idempotente com apps completas).
            .init_resource::<Assets<Mesh>>()
            // shake_on_player_hurt precisa do trauma em qualquer app.
            .init_resource::<crate::camera::CameraShake>()
            .add_systems(
                Startup,
                (
                    spawn_vignette,
                    spawn_low_hp_vignette,
                    // exclusivo: precisa de `&mut World` para o HudAssets
                    spawn_damage_number_pool,
                    spawn_target_ring_assets,
                ),
            )
            // O ensure do `Invulnerable` corre ANTES do primeiro dano do
            // herói — com um sync point entre os dois (o `auto_insert_
            // apply_deferred` insere-o na aresta `.before`).
            .add_systems(
                Update,
                ensure_player_invulnerable.before(player_hurt_system),
            )
            .add_systems(
                Update,
                (
                    player_hurt_system,
                    tick_status_system,
                    respawn_system,
                    decay_invulnerability,
                    target_expiry_system,
                    target_ring_system,
                    vignette_system,
                    low_hp_vignette_system,
                    damage_numbers_system,
                    shake_on_player_hurt,
                    hit_flash_system,
                ),
            );
    }
}

// ── spawn de UI ─────────────────────────────────────────────────────────

fn spawn_vignette(mut commands: Commands) {
    commands.spawn((
        Node {
            position_type: PositionType::Absolute,
            top: Val::Px(0.0),
            left: Val::Px(0.0),
            width: Val::Percent(100.0),
            height: Val::Percent(100.0),
            border: UiRect::all(Val::Px(72.0)),
            ..Default::default()
        },
        BackgroundColor(Color::NONE),
        BorderColor::all(Color::NONE),
        Name::new("fx:hurt-vignette"),
        HurtVignette,
    ));
}

#[derive(Component)]
struct HurtVignette;

/// Vinheta de HP baixo (persistente, mesmo padrão do [`HurtVignette`]):
/// acorda quando a fração de HP passa abaixo de [`LOW_HP_FRACTION`].
#[derive(Component)]
struct LowHpVignette;

fn spawn_low_hp_vignette(mut commands: Commands) {
    commands.spawn((
        Node {
            position_type: PositionType::Absolute,
            top: Val::Px(0.0),
            left: Val::Px(0.0),
            width: Val::Percent(100.0),
            height: Val::Percent(100.0),
            border: UiRect::all(Val::Px(72.0)),
            ..Default::default()
        },
        BackgroundColor(Color::NONE),
        BorderColor::all(Color::NONE),
        Name::new("fx:lowhp-vignette"),
        LowHpVignette,
    ));
}

/// Pool de números de dano — sistema EXCLUSIVO porque a fonte (Cinzel, a
/// mesma do HUD) vive em [`crate::hud::assets::HudAssets`], cujo `get` pede
/// `&mut World` (lazy-init do recurso + assets). Apps mínimas não têm os
/// registos do AssetPlugin: init aqui, idempotente.
fn spawn_damage_number_pool(world: &mut World) {
    if world.get_resource::<Assets<Font>>().is_none() {
        world.init_resource::<Assets<Font>>();
    }
    if world.get_resource::<Assets<Image>>().is_none() {
        world.init_resource::<Assets<Image>>();
    }
    let font = crate::hud::assets::HudAssets::get(world).font;
    for i in 0..NUMBER_POOL {
        world.spawn((
            Node {
                position_type: PositionType::Absolute,
                left: Val::Px(0.0),
                top: Val::Px(0.0),
                ..Default::default()
            },
            Text::new(""),
            TextColor(Color::NONE),
            TextFont {
                font: font.clone().into(),
                font_size: 30.0.into(),
                ..Default::default()
            },
            Name::new(format!("fx:dmg-{i}")),
            Visibility::Hidden,
            DamageNumberSlot {
                world_pos: Vec3::ZERO,
                age: 0.0,
                active: false,
                base_font_size: 30.0,
            },
        ));
    }
}

// ── sistemas ────────────────────────────────────────────────────────────

/// Garante `Invulnerable` no herói (padrão `ensure_*` de `combat.rs`):
/// timer 0 = componente presente, janela fechada (`hurt_player` só bloqueia
/// com `timer > 0`). Pré-inserir resolve o 1.º dano da sessão: antes, o
/// componente entrava via Commands DEPOIS do dano e um 2.º `PlayerHurt` no
/// MESMO frame aplicava também (i-frames furados no frame do spawn).
#[allow(clippy::type_complexity)]
fn ensure_player_invulnerable(
    players: Query<(Entity, Option<&Invulnerable>), With<Player>>,
    mut commands: Commands,
) {
    for (entity, invuln) in &players {
        if invuln.is_none() {
            commands.entity(entity).insert(Invulnerable { timer: 0.0 });
        }
    }
}

/// Consome `PlayerHurt`: i-frames, HP, número flutuante, vinheta.
#[allow(clippy::type_complexity)]
#[allow(clippy::too_many_arguments)]
fn player_hurt_system(
    mut hurts: MessageReader<PlayerHurt>,
    mut players: Query<
        (
            Entity,
            &GlobalTransform,
            &mut Health,
            Option<&mut Invulnerable>,
            Option<&Dying>,
            Option<&Guarding>,
        ),
        With<Player>,
    >,
    mut commands: Commands,
    mut numbers: MessageWriter<DamageNumberEvent>,
    mut flash: ResMut<HurtFlash>,
    mut toasts: MessageWriter<ScriptToast>,
    mut sfx: MessageWriter<crate::ambient::SfxEvent>,
    mut knockbacks: Query<&mut crate::physics_fx::Knockback>,
) {
    let Ok((entity, transform, mut health, mut invuln, dying, guard)) = players.single_mut() else {
        return;
    };
    for hurt in hurts.read() {
        // guard [L]: parry total na janela inicial, senão −75 %
        let mut amount = hurt.amount;
        if !hurt.status {
            if let Some(g) = guard {
                if g.timer <= crate::skills::PARRY_WINDOW {
                    amount = 0.0;
                    toasts.write(ScriptToast("PARRY!".into()));
                    sfx.write(crate::ambient::SfxEvent {
                        clip: crate::ambient::SfxClip::ShieldBlock,
                        position: Some(transform.translation()),
                    });
                } else {
                    amount *= GUARD_REDUCTION;
                    sfx.write(crate::ambient::SfxEvent {
                        clip: crate::ambient::SfxClip::ShieldBlock,
                        position: Some(transform.translation()),
                    });
                }
            }
        }
        if amount <= 0.0 {
            continue;
        }
        match hurt_player(&mut health, invuln.as_deref(), dying, amount, hurt.status) {
            HurtOutcome::Ignored | HurtOutcome::Blocked => continue,
            HurtOutcome::Applied { killed } => {
                sfx.write(crate::ambient::SfxEvent {
                    clip: if killed {
                        // Morte do herói: sting de derrota (interface, sem
                        // atenuação por distância).
                        crate::ambient::SfxClip::GameOver
                    } else {
                        crate::ambient::SfxClip::Hurt
                    },
                    position: if killed {
                        None
                    } else {
                        Some(transform.translation())
                    },
                });
            }
        }
        if !hurt.status {
            // i-frames físicos renovam a cada golpe
            if let Some(frame) = invuln.as_deref_mut() {
                frame.timer = IFRAME_SECS;
            } else {
                commands
                    .entity(entity)
                    .insert(Invulnerable { timer: IFRAME_SECS });
            }
            // Hit-react: o herói flincha (one-shot `hit` com guard 0,4 s em
            // `animation::hit_react_system` — dano de status não flinch).
            commands.entity(entity).insert(crate::animation::HitReact);
            numbers.write(DamageNumberEvent {
                position: transform.translation() + Vec3::Y * 1.9,
                text: format!("-{}", amount.round() as i32),
                color: Color::srgb(1.0, 0.32, 0.25),
            });
        }
        if amount > 0.0 {
            flash.0 = (flash.0 + if hurt.status { 0.35 } else { 0.9 }).min(1.0);
            // knockback do herói na direção do atacante
            if let Some(from) = hurt.from {
                let dir = (transform.translation() - from).normalize_or_zero();
                let kb = crate::physics_fx::knockback_after(dir, 5.0);
                if kb.velocity.length_squared() > 0.0 {
                    if let Ok(mut existing) = knockbacks.get_mut(entity) {
                        existing.velocity = kb.velocity;
                    } else {
                        commands.entity(entity).insert(kb);
                    }
                }
            }
        }
    }
}

/// Dano recebido abana a câmara (peso `min(0.45, 0.22 + dmg/90)` — VibeGame
/// `CombatFeedbackSystem`). Ticks de status não abanam.
pub fn shake_on_player_hurt(
    mut hurts: MessageReader<PlayerHurt>,
    mut shake: ResMut<crate::camera::CameraShake>,
) {
    for hurt in hurts.read() {
        if hurt.status {
            continue;
        }
        crate::camera::add_camera_shake(&mut shake, (0.22 + hurt.amount / 90.0).min(0.45));
    }
}

/// Veneno: 1 tick/s enquanto activo — passa pelo único caminho de dano
/// (`PlayerHurt` → `player_hurt_system`: sem i-frames, sem número,
/// respeitando `Dying`). Não aplicar aqui directamente: o evento era
/// consumido a seguir e o tick saía em DUPLICO.#[allow(clippy::type_complexity)]
fn tick_status_system(
    time: Res<Time>,
    mut players: Query<&mut StatusEffects, With<Player>>,
    mut hurts: MessageWriter<PlayerHurt>,
) {
    let dt = time.delta_secs();
    for mut effects in &mut players {
        let tick = tick_venom(&mut effects, dt);
        if tick > 0.0 {
            hurts.write(PlayerHurt {
                amount: tick,
                status: true,
                from: None,
            });
        }
    }
}

/// Morte do herói → espera → respawn no ponto mais próximo, HP cheio.
#[allow(clippy::type_complexity)]
fn respawn_system(
    mut players: Query<
        (
            Entity,
            &mut Health,
            &mut Transform,
            Option<&mut Dying>,
            Option<&mut StatusEffects>,
        ),
        With<Player>,
    >,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    time: Res<Time>,
    mut commands: Commands,
    mut toasts: MessageWriter<ScriptToast>,
) {
    let dt = time.delta_secs();
    for (entity, mut health, mut transform, dying, mut status) in &mut players {
        match dying {
            Some(mut state) => {
                state.timer -= dt;
                if state.timer <= 0.0 {
                    let death_xz = Vec2::new(transform.translation.x, transform.translation.z);
                    let point = nearest_respawn_point(death_xz);
                    // SUPERFÍCIE RENDERIZADA (paridade com spawners/knockback):
                    // o sample analítico flutua acima do mesh nas cristas —
                    // renascia-se a "pairar" sobre os pontos de respawn.
                    let y = terrain
                        .as_deref()
                        .map(|t| t.sample_mesh_surface(point.x, point.y) + 0.1)
                        .unwrap_or(transform.translation.y);
                    transform.translation = Vec3::new(point.x, y, point.y);
                    health.current = health.max;
                    // Morte limpa status: renascer envenenado punha o herói a
                    // perder o HP cheio no ponto de respawn sem inimigo algum.
                    if let Some(effects) = status.as_deref_mut() {
                        effects.venom = 0.0;
                        effects.venom_tick = 0.0;
                    }
                    commands.entity(entity).remove::<Dying>();
                    commands.entity(entity).insert(Invulnerable {
                        timer: RESPAWN_DELAY,
                    });
                    toasts.write(ScriptToast(format!(
                        "De volta à {} — levanta e luta!",
                        respawn_label(point)
                    )));
                    info!(
                        target: "viber::feedback",
                        "respawn na {} ({point:?}) — HP cheio + {RESPAWN_DELAY}s de i-frames",
                        respawn_label(point)
                    );
                }
            }
            None => {
                if health.current <= 0.0 {
                    commands.entity(entity).insert(Dying {
                        timer: RESPAWN_DELAY,
                    });
                    toasts.write(ScriptToast("Caiu em combate…".into()));
                    info!(target: "viber::feedback", "herói caiu — respawn em {RESPAWN_DELAY}s");
                }
            }
        }
    }
}

/// Decrementa a janela de i-frames.
fn decay_invulnerability(time: Res<Time>, mut frames: Query<&mut Invulnerable>) {
    let dt = time.delta_secs();
    for mut frame in &mut frames {
        frame.timer -= dt;
    }
}

/// Expira o alvo de combate (TTL) e limpa alvos mortos.
fn target_expiry_system(
    time: Res<Time>,
    mut target: ResMut<CombatTarget>,
    alive: Query<(Entity, &Health)>,
) {
    if let Some(entity) = target.entity {
        let exists = alive.get(entity).is_ok_and(|(_, hp)| hp.current > 0.0);
        if !exists {
            target.entity = None;
            target.timer = 0.0;
            return;
        }
        target.timer -= time.delta_secs();
        if target.timer <= 0.0 {
            target.entity = None;
        }
    }
}

// ── anel de alvo ────────────────────────────────────────────────────────

/// Raios do anel (torus flat no plano XZ): interior/exterior.
const RING_INNER: f32 = 0.78;
const RING_OUTER: f32 = 0.94;
/// Hz do pulso de escala/alpha do anel.
pub const RING_PULSE_HZ: f32 = 1.6;
/// Amplitude do pulso de escala (±8 %).
pub const RING_SCALE_AMPLITUDE: f32 = 0.08;
/// Alpha do anel: mínimo e máximo do pulso.
const RING_ALPHA_MIN: f32 = 0.40;
const RING_ALPHA_MAX: f32 = 0.75;
/// Altura do anel acima dos pés do alvo (evita z-fight com o chão).
const RING_Y_OFFSET: f32 = 0.06;

/// Mesh + material partilhados do anel (um anel VIVO de cada vez — o pulso
/// de alpha pode mutar o material partilhado sem surpresas).
#[derive(Resource)]
struct TargetRingAssets {
    mesh: Handle<Mesh>,
    material: Handle<StandardMaterial>,
}

/// Anel vivo: a que entidade segue e a idade (para o pulso).
#[derive(Debug, Component)]
struct TargetRing {
    target: Entity,
    age: f32,
}

/// Pulso do anel para uma idade (s): (escala uniforme, alpha). Escala pulsa
/// ±[`RING_SCALE_AMPLITUDE`] em torno de 1.0; alpha oscila entre
/// [`RING_ALPHA_MIN`] e [`RING_ALPHA_MAX`] em fase. Puro para testes.
pub fn target_ring_pulse(age: f32) -> (f32, f32) {
    let wave = (std::f32::consts::TAU * RING_PULSE_HZ * age).sin();
    (
        1.0 + RING_SCALE_AMPLITUDE * wave,
        RING_ALPHA_MIN + (RING_ALPHA_MAX - RING_ALPHA_MIN) * (0.5 + 0.5 * wave),
    )
}

fn spawn_target_ring_assets(
    mut commands: Commands,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
) {
    let mesh = meshes.add(Torus::new(RING_INNER, RING_OUTER));
    let material = materials.add(StandardMaterial {
        base_color: Color::srgba(1.0, 0.16, 0.12, 0.7),
        emissive: LinearRgba::rgb(1.1, 0.06, 0.04),
        unlit: true,
        alpha_mode: AlphaMode::Blend,
        ..StandardMaterial::default()
    });
    commands.insert_resource(TargetRingAssets { mesh, material });
}

/// Anel de alvo: sempre que [`CombatTarget`] aponta para uma entidade viva,
/// existe UM torus flat unlit vermelho nos pés dela — pulsa escala (±8 %) e
/// alpha, segue a posição por frame e despawna quando o alvo se perde
/// (TTL/morte/alvo outro). Sem texturas: mesh + material emissivo.
#[allow(clippy::type_complexity)]
fn target_ring_system(
    mut commands: Commands,
    time: Res<Time>,
    target: Res<CombatTarget>,
    positions: Query<&GlobalTransform>,
    mut rings: Query<(
        Entity,
        &mut TargetRing,
        &mut Transform,
        &MeshMaterial3d<StandardMaterial>,
    )>,
    assets: Option<Res<TargetRingAssets>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
) {
    let wanted = target.entity;
    // Sincroniza: anel cujo alvo já não é o atual → despawn.
    for (entity, ring, ..) in &mut rings {
        if Some(ring.target) != wanted {
            commands.entity(entity).despawn();
        }
    }
    let Some(assets) = assets else {
        return;
    };
    let Some(wanted) = wanted else {
        return;
    };
    let alive = rings.iter().any(|(_, ring, _, _)| ring.target == wanted);
    if !alive {
        if let Ok(t) = positions.get(wanted) {
            commands.spawn((
                Mesh3d(assets.mesh.clone()),
                MeshMaterial3d(assets.material.clone()),
                Transform::from_translation(t.translation() + Vec3::Y * RING_Y_OFFSET),
                Visibility::Inherited,
                NotShadowCaster,
                Name::new("fx:target-ring"),
                TargetRing {
                    target: wanted,
                    age: 0.0,
                },
            ));
        }
    }
    // Follow + pulso por frame.
    let dt = time.delta_secs();
    for (_entity, mut ring, mut transform, material) in &mut rings {
        ring.age += dt;
        if let Ok(t) = positions.get(ring.target) {
            transform.translation = t.translation() + Vec3::Y * RING_Y_OFFSET;
        }
        let (scale, alpha) = target_ring_pulse(ring.age);
        transform.scale = Vec3::splat(scale);
        if let Some(mut material) = materials.get_mut(&material.0) {
            material.base_color.set_alpha(alpha);
        }
    }
}

/// Vinheta: decai e aplica as alphas (fundo + borda).
fn vignette_system(
    mut flash: ResMut<HurtFlash>,
    time: Res<Time>,
    mut q_vignette: Query<(&mut BackgroundColor, &mut BorderColor), With<HurtVignette>>,
) {
    flash.0 = (flash.0 - time.delta_secs() * 2.2).max(0.0);
    let intensity = flash.0;
    let Ok((mut bg, mut border)) = q_vignette.single_mut() else {
        return;
    };
    bg.0 = Color::srgba(0.62, 0.05, 0.05, 0.28 * intensity);
    *border = BorderColor::all(Color::srgba(0.55, 0.03, 0.03, 0.55 * intensity));
}

// ── vinheta de HP baixo ─────────────────────────────────────────────────

/// Fração de HP abaixo da qual a vinheta persistente pulsa.
pub const LOW_HP_FRACTION: f32 = 0.30;
/// Frequência do pulso (Hz).
pub const LOW_HP_PULSE_HZ: f32 = 1.1;
/// Fade in/out da vinheta (s) — a vinheta não pisca a entrar nem a sair.
pub const LOW_HP_FADE_SECS: f32 = 0.5;

/// Fade suavizado da vinheta de HP baixo: move `current` para `target`
/// (0/1) linearmente ao longo de [`LOW_HP_FADE_SECS`]. Puro para testes.
pub fn low_hp_fade(current: f32, target: f32, dt: f32) -> f32 {
    let step = (dt / LOW_HP_FADE_SECS).max(0.0);
    if target > current {
        (current + step).min(target)
    } else {
        (current - step).max(target)
    }
}

/// Alpha da vinheta de HP baixo: fade suavizado × pulso senoidal
/// ([`LOW_HP_PULSE_HZ`]) × profundidade (∝ (0.30 − fração)/0.30, clampada).
/// Frações ≥ 0.30 devolvem 0. Puro para testes.
pub fn low_hp_alpha(fraction: f32, fade: f32, t: f32) -> f32 {
    let depth = ((LOW_HP_FRACTION - fraction) / LOW_HP_FRACTION).clamp(0.0, 1.0);
    let pulse = 0.5 + 0.5 * (std::f32::consts::TAU * LOW_HP_PULSE_HZ * t).sin();
    (fade * pulse * depth).clamp(0.0, 1.0)
}

/// Vinheta de HP baixo: borda vermelho-escura pulsante enquanto a fração de
/// HP do herói está abaixo de [`LOW_HP_FRACTION`]. Coexiste com a vinheta de
/// dano ([`vignette_system`]) — nós separados, cada um escreve o seu.
#[allow(clippy::type_complexity)]
fn low_hp_vignette_system(
    time: Res<Time>,
    players: Query<&Health, With<Player>>,
    mut fade: Local<f32>,
    mut q_vignette: Query<(&mut BackgroundColor, &mut BorderColor), With<LowHpVignette>>,
) {
    let Ok((mut bg, mut border)) = q_vignette.single_mut() else {
        return;
    };
    let fraction = players
        .single()
        .ok()
        .map(|hp| {
            if hp.max > 0.0 {
                hp.current / hp.max
            } else {
                0.0
            }
        })
        // Sem herói (apps mínimas): a vinheta esvazia.
        .unwrap_or(1.0);
    let target = if fraction < LOW_HP_FRACTION { 1.0 } else { 0.0 };
    *fade = low_hp_fade(*fade, target, time.delta_secs());
    let alpha = low_hp_alpha(fraction, *fade, time.elapsed_secs());
    bg.0 = Color::srgba(0.45, 0.02, 0.02, 0.16 * alpha);
    *border = BorderColor::all(Color::srgba(0.55, 0.03, 0.03, 0.55 * alpha));
}

/// Entidades da subárvore (raiz incluída) — os GLBs spawnam os meshes como
/// filhos; o flash tem de os alcançar todos. `pub(crate)`: o fade de corpses
/// (`combat.rs`) percorre a mesma subárvore.
pub(crate) fn collect_subtree(root: Entity, children: &Query<&Children>, out: &mut Vec<Entity>) {
    out.push(root);
    for child in children.get(root).into_iter().flatten() {
        collect_subtree(*child, children, out);
    }
}

/// Hit-flash do inimigo: no 1.º frame clona os materiais da subárvore (os
/// assets são partilhados entre instâncias — mutar o original acenderia
/// TODOS os lobos quando um apanha), troca os handles e acende-os em
/// branco-quente; nos seguintes, anima o emissive até apagar. No fim, REVERTE
/// a troca e remove os clones — restaurar o original é até mais fiel do que
/// o comportamento antigo (deixava um clone com emissive a PRETO, que apagava
/// o brilho de materiais emissivos originais) e fecha o leak.
#[allow(clippy::type_complexity)]
fn hit_flash_system(
    time: Res<Time>,
    mut materials: ResMut<Assets<StandardMaterial>>,
    mut flashed: Query<(Entity, &mut HitFlash, Option<&mut FlashMaterials>), Without<Player>>,
    mut mesh_materials: Query<&mut MeshMaterial3d<StandardMaterial>>,
    children: Query<&Children>,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    for (entity, mut flash, mut owned) in &mut flashed {
        flash.timer -= dt;
        if flash.timer <= 0.0 {
            if let Some(owned) = owned.as_mut() {
                for (node, original, clone) in owned.0.iter() {
                    // Só reverte se o handle do nó ainda FOR o nosso clone:
                    // se entretanto outra coisa o trocou (o cadáver clona de
                    // novo para o fade), o clone já não está referenciado e
                    // removê-lo é seguro de qualquer forma.
                    if let Ok(mut slot) = mesh_materials.get_mut(*node)
                        && slot.0 == *clone
                    {
                        slot.0 = original.clone();
                    }
                    materials.remove(clone);
                }
            }
            commands
                .entity(entity)
                .remove::<(HitFlash, FlashMaterials)>();
            continue;
        }
        let intensity = hit_flash_intensity(flash.timer);
        let emissive = LinearRgba::rgb(
            HIT_FLASH_EMISSIVE * intensity,
            HIT_FLASH_EMISSIVE * 0.88 * intensity,
            HIT_FLASH_EMISSIVE * 0.68 * intensity,
        );
        if let Some(owned) = owned.as_mut() {
            for (_, _, clone) in owned.0.iter() {
                if let Some(mut material) = materials.get_mut(clone) {
                    material.emissive = emissive;
                }
            }
        } else {
            // Primeiro frame do flash: clona e troca (uma vez por flash).
            let mut subtree = Vec::new();
            collect_subtree(entity, &children, &mut subtree);
            let mut cloned: Vec<(Entity, Handle<StandardMaterial>, Handle<StandardMaterial>)> =
                Vec::new();
            for node in subtree {
                if cloned.len() >= MAX_FLASH_MATS {
                    break;
                }
                let Ok(mut slot) = mesh_materials.get_mut(node) else {
                    continue;
                };
                let Some(original) = materials.get(&slot.0) else {
                    continue;
                };
                let mut copy = original.clone();
                copy.emissive = emissive;
                let handle = materials.add(copy);
                let original = std::mem::replace(&mut slot.0, handle.clone());
                cloned.push((node, original, handle));
            }
            commands.entity(entity).insert(FlashMaterials(cloned));
        }
    }
}

/// Números de dano: projecta no ecrã, sobe e desvanece. O tamanho da fonte
/// acompanha o tipo de golpe ([`number_font_size`]) — crítico/remate dominam
/// — e os primeiros ~0.1 s têm um pop de escala com overshoot
/// ([`damage_number_scale`]).
#[allow(clippy::type_complexity)]
fn damage_numbers_system(
    time: Res<Time>,
    mut incoming: MessageReader<DamageNumberEvent>,
    mut slots: Query<(
        &mut DamageNumberSlot,
        &mut Node,
        &mut Text,
        &mut TextColor,
        &mut TextFont,
        &mut Visibility,
    )>,
    camera: Query<(&Camera, &GlobalTransform), With<Camera3d>>,
) {
    for event in incoming.read() {
        let font_size = number_font_size(&event.text);
        for (mut slot, _node, mut text, mut color, mut font, mut visibility) in &mut slots {
            if slot.active {
                continue;
            }
            slot.active = true;
            slot.world_pos = event.position;
            slot.age = 0.0;
            slot.base_font_size = font_size;
            *text = Text::new(event.text.clone());
            color.0 = event.color;
            font.font_size = font_size.into();
            *visibility = Visibility::Inherited;
            info!(target: "viber::feedback", "número '{}' ativado em {:#?}", event.text, event.position);
            break;
        }
    }
    let Ok((camera, camera_transform)) = camera.single() else {
        return;
    };
    let dt = time.delta_secs();
    for (mut slot, mut node, _text, mut color, mut font, mut visibility) in &mut slots {
        if !slot.active {
            continue;
        }
        slot.age += dt;
        if slot.age >= NUMBER_LIFETIME {
            slot.active = false;
            *visibility = Visibility::Hidden;
            color.0.set_alpha(0.0);
            continue;
        }
        let projected = camera.world_to_viewport(
            camera_transform,
            slot.world_pos + Vec3::Y * (slot.age * 0.6),
        );
        let Ok(screen) = projected else {
            // pode acontecer em apps sem viewport (headless/tests)
            debug!(target: "viber::feedback", "projeção falhou: {projected:?}");
            *visibility = Visibility::Hidden;
            continue;
        };
        if slot.age - dt < 0.05 {
            info!(target: "viber::feedback", "número projetado em {screen:?}");
        }
        // world_to_viewport devolve o canto superior-esquerdo em px lógicos;
        // o texto fica um pouco acima do ponto e centrado a olho.
        node.left = Val::Px(screen.x - 18.0);
        node.top = Val::Px(screen.y - 34.0);
        let fade = (1.0 - slot.age / NUMBER_LIFETIME).clamp(0.0, 1.0);
        color.0.set_alpha(fade);
        // Pop de escala: overshoot 1.25× nos primeiros 40 ms, assente a 1.0
        // a partir de ~100 ms (escala no tamanho da fonte — bevy_ui não tem
        // transform de escala de texto fiável).
        font.font_size = (slot.base_font_size * damage_number_scale(slot.age)).into();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_nearest_respawn_point() {
        // morto no centro → praça
        assert_eq!(nearest_respawn_point(Vec2::new(3.0, -4.0)), Vec2::ZERO);
        // morto perto do portão leste
        assert_eq!(
            nearest_respawn_point(Vec2::new(48.0, 2.0)),
            Vec2::new(50.0, 0.0)
        );
        // morto longe no deserto → o mais próximo dos 5 ainda é escolhido
        let far = nearest_respawn_point(Vec2::new(-290.0, 12.0));
        assert_eq!(far, Vec2::new(-50.0, 0.0));
    }

    #[test]
    fn test_respawn_labels() {
        assert_eq!(respawn_label(Vec2::ZERO), "praça");
        assert_eq!(respawn_label(Vec2::new(0.0, -50.0)), "portão sul");
        assert_eq!(respawn_label(Vec2::new(0.0, 50.0)), "portão norte");
        assert_eq!(respawn_label(Vec2::new(-50.0, 0.0)), "portão oeste");
        assert_eq!(respawn_label(Vec2::new(50.0, 0.0)), "portão leste");
    }

    #[test]
    fn test_hurt_player_gates() {
        let mut hp = Health::default();
        // i-frames bloqueiam dano físico…
        let invuln = Invulnerable { timer: 0.2 };
        assert_eq!(
            hurt_player(&mut hp, Some(&invuln), None, 25.0, false),
            HurtOutcome::Blocked
        );
        assert!((hp.current - 100.0).abs() < 1e-4, "não perdeu HP");
        // …mas dano de status passa
        assert_eq!(
            hurt_player(&mut hp, Some(&invuln), None, 25.0, true),
            HurtOutcome::Applied { killed: false }
        );
        // morto (Dying) ignora tudo
        let dying = Dying { timer: 1.0 };
        assert_eq!(
            hurt_player(&mut hp, None, Some(&dying), 50.0, true),
            HurtOutcome::Ignored
        );
    }

    #[test]
    fn test_iframes_expire_by_timer() {
        // o componente permanece na entidade; é o TIMER que bloqueia —
        // janela expirada (<= 0) não pode bloquear dano físico
        let mut hp = Health::default();
        let expired = Invulnerable { timer: 0.0 };
        assert_eq!(
            hurt_player(&mut hp, Some(&expired), None, 25.0, false),
            HurtOutcome::Applied { killed: false },
            "i-frame expirado (componente presente, timer 0) não bloqueia"
        );
        assert!((hp.current - 75.0).abs() < 1e-4);
    }

    #[test]
    fn test_hurt_player_kills() {
        let mut hp = Health {
            current: 10.0,
            max: 100.0,
        };
        assert_eq!(
            hurt_player(&mut hp, None, None, 25.0, false),
            HurtOutcome::Applied { killed: true }
        );
        assert!((hp.current - 0.0).abs() < 1e-4);
    }

    #[test]
    fn test_venom_ticks_once_per_second() {
        let mut effects = StatusEffects {
            venom: 2.5,
            venom_tick: 0.0,
        };
        let mut total = 0.0;
        // 2.5 s em passos de 0.25 s (exatos em binário): ticks no 4.º e 8.º
        // passo; o veneno esgota no 10.º
        for _ in 0..10 {
            total += tick_venom(&mut effects, 0.25);
        }
        assert!((total - VENOM_DPS * 2.0).abs() < 1e-6, "total {total}");
        assert!(effects.venom <= 0.0, "veneno expirou: {}", effects.venom);
        // veneno zerado não volta a ticar
        assert_eq!(tick_venom(&mut effects, 0.25), 0.0);
    }

    #[test]
    fn test_venom_inactive_is_free() {
        let mut effects = StatusEffects::default();
        assert_eq!(tick_venom(&mut effects, 0.5), 0.0);
    }

    #[test]
    fn test_damage_number_slot_activates_and_projects() {
        let mut app = App::new();
        app.add_plugins(MinimalPlugins);
        app.add_plugins(FeedbackPlugin);
        app.world_mut().spawn((
            Camera3d::default(),
            Camera::default(),
            Projection::default(),
            GlobalTransform::from(Transform::from_xyz(0.0, 2.0, 6.0)),
        ));
        app.update(); // Startup: pool + vignette
        app.world_mut().write_message(DamageNumberEvent {
            position: Vec3::new(0.0, 1.5, 0.0),
            text: "-10".into(),
            color: Color::srgb(1.0, 0.3, 0.2),
        });
        app.update(); // leitura do evento + projeção

        let world = app.world_mut();
        let mut q = world.query::<(&DamageNumberSlot, &Visibility, &Node, &Text)>();
        let mut active = 0;
        for (slot, _visibility, _node, text) in q.iter(world) {
            if slot.active {
                active += 1;
                assert_eq!(text.0, "-10");
            }
        }
        assert_eq!(active, 1, "exatamente um slot ativado pelo evento");
        // Nota: a projeção (world_to_viewport) precisa de viewport real —
        // headless ela esconde o slot; o render é verificado in-game via
        // screenshots da bridge (validação do loop).
    }

    #[test]
    fn test_number_font_size_ranks_impact() {
        assert_eq!(number_font_size("-25"), 30.0);
        assert_eq!(number_font_size("-50 x2"), 38.0, "backstab");
        assert_eq!(number_font_size("EXECUTADO!"), 38.0);
        assert_eq!(number_font_size("GOLPE FINAL!"), 38.0);
        assert_eq!(number_font_size("-63 CRIT!"), 46.0, "crítico domina");
    }

    #[test]
    fn test_hit_flash_intensity_quadratic_falloff() {
        assert_eq!(hit_flash_intensity(HIT_FLASH_SECS), 1.0, "pico");
        let mid = hit_flash_intensity(HIT_FLASH_SECS * 0.5);
        assert!((mid - 0.25).abs() < 1e-5, "quadrática: {mid}");
        assert_eq!(hit_flash_intensity(0.0), 0.0);
        assert_eq!(hit_flash_intensity(-1.0), 0.0, "clamp em baixo");
        assert_eq!(
            hit_flash_intensity(HIT_FLASH_SECS * 4.0),
            1.0,
            "clamp em cima"
        );
    }

    // ── pop de escala dos números de dano ────────────────────────────────

    #[test]
    fn test_damage_number_scale_overshoots_and_settles() {
        // Arranca em 1.0, pico de 1.25× exato aos 40 ms, assente em 1.0.
        assert!((damage_number_scale(0.0) - 1.0).abs() < 1e-5);
        assert!(
            (damage_number_scale(-1.0) - 1.0).abs() < 1e-5,
            "idade inválida = 1.0"
        );
        assert!((damage_number_scale(NUMBER_POP_PEAK_AT) - NUMBER_POP_PEAK).abs() < 1e-4);
        assert!((damage_number_scale(NUMBER_POP_SETTLE_AT) - 1.0).abs() < 1e-4);
        assert!(
            (damage_number_scale(0.5) - 1.0).abs() < 1e-5,
            "passado o pop"
        );
        // Dentro da subida: entre 1.0 e o pico, monotónico crescente.
        let quarter = damage_number_scale(NUMBER_POP_PEAK_AT * 0.25);
        assert!(quarter > 1.0 && quarter < NUMBER_POP_PEAK, "{quarter}");
        let half = damage_number_scale(NUMBER_POP_PEAK_AT * 0.5);
        assert!(half > quarter && half < NUMBER_POP_PEAK, "{half}");
        // Na descida: entre o pico e 1.0, monotónico decrescente.
        let down = damage_number_scale((NUMBER_POP_PEAK_AT + NUMBER_POP_SETTLE_AT) * 0.5);
        assert!(down > 1.0 && down < NUMBER_POP_PEAK, "{down}");
    }

    // ── vinheta de HP baixo ──────────────────────────────────────────────

    #[test]
    fn test_low_hp_alpha_depth_and_pulse() {
        let t = 1.23; // instante qualquer
        // HP cheio: nada, mesmo com fade a 1.
        assert_eq!(low_hp_alpha(1.0, 1.0, t), 0.0);
        // Exatamente no limiar: profundidade 0 → invisível.
        assert_eq!(low_hp_alpha(LOW_HP_FRACTION, 1.0, t), 0.0);
        // A zero de HP, no pico do pulso: alpha = fade.
        let peak_t = 0.25 / LOW_HP_PULSE_HZ; // sin = 1 → pulso = 1
        assert!((low_hp_alpha(0.0, 1.0, peak_t) - 1.0).abs() < 1e-4);
        // Metade da profundidade: metade do alpha (mesmo instante).
        let half = low_hp_alpha(LOW_HP_FRACTION * 0.5, 1.0, peak_t);
        assert!((half - 0.5).abs() < 1e-4, "{half}");
        // Pulso é senoidal: há instantes com alpha 0 mesmo a HP 0.
        let trough_t = 0.75 / LOW_HP_PULSE_HZ; // sin = -1 → pulso = 0
        assert!(low_hp_alpha(0.0, 1.0, trough_t) < 1e-4);
        // Fade a 0: nada.
        assert_eq!(low_hp_alpha(0.0, 0.0, t), 0.0);
    }

    #[test]
    fn test_low_hp_fade_reaches_target_in_half_a_second() {
        // Sobe 0→1 em LOW_HP_FADE_SECS.
        let mut f = 0.0;
        let dt = 1.0 / 60.0;
        for _ in 0..60 {
            f = low_hp_fade(f, 1.0, dt);
        }
        assert!((f - 1.0).abs() < 1e-4, "fade in completo: {f}");
        // Desce 1→0 no mesmo tempo.
        for _ in 0..60 {
            f = low_hp_fade(f, 0.0, dt);
        }
        assert!(f.abs() < 1e-4, "fade out completo: {f}");
        // dt 0 não mexe; clamp nos extremos.
        assert_eq!(low_hp_fade(0.4, 1.0, 0.0), 0.4);
        assert_eq!(low_hp_fade(0.4, 1.0, 10.0), 1.0, "clamp no alvo");
    }

    // ── anel de alvo ─────────────────────────────────────────────────────

    #[test]
    fn test_target_ring_pulse_bounds() {
        let (mut min_scale, mut max_scale) = (f32::MAX, f32::MIN);
        let (mut min_alpha, mut max_alpha) = (f32::MAX, f32::MIN);
        for i in 0..200 {
            let (scale, alpha) = target_ring_pulse(i as f32 / 60.0);
            assert!(
                (1.0 - RING_SCALE_AMPLITUDE..=1.0 + RING_SCALE_AMPLITUDE).contains(&scale),
                "escala fora de ±8 %: {scale}"
            );
            min_scale = min_scale.min(scale);
            max_scale = max_scale.max(scale);
            min_alpha = min_alpha.min(alpha);
            max_alpha = max_alpha.max(alpha);
        }
        // O pulso realmente oscila (não é constante).
        assert!(max_scale - min_scale > 0.1);
        assert!(max_alpha - min_alpha > 0.2);
        assert!((RING_ALPHA_MIN..=RING_ALPHA_MAX).contains(&min_alpha));
        assert!((RING_ALPHA_MIN..=RING_ALPHA_MAX).contains(&max_alpha));
    }

    #[test]
    fn test_hit_flash_clones_material_and_restores() {
        use bevy::mesh::PrimitiveTopology;
        let mut app = App::new();
        app.add_plugins(MinimalPlugins);
        app.add_plugins(FeedbackPlugin);
        app.init_resource::<Assets<bevy::mesh::Mesh>>();
        app.update(); // Startup: pool + vignette + Assets registado

        let world = app.world_mut();
        let mesh = world
            .resource_mut::<Assets<bevy::mesh::Mesh>>()
            .add(bevy::mesh::Mesh::new(
                PrimitiveTopology::TriangleList,
                bevy::asset::RenderAssetUsages::MAIN_WORLD,
            ));
        let original = world
            .resource_mut::<Assets<StandardMaterial>>()
            .add(StandardMaterial {
                base_color: Color::srgb(0.8, 0.2, 0.1),
                ..Default::default()
            });
        // Linha de base DEPOIS de criar o material do teste: o FeedbackPlugin
        // também tem materiais próprios (vinheta, …), portanto o que se mede
        // é o DELTA do flash, não a contagem absoluta do store.
        let baseline = world.resource::<Assets<StandardMaterial>>().len();
        let enemy = world
            .spawn((Mesh3d(mesh), MeshMaterial3d(original.clone())))
            .id();
        world.entity_mut(enemy).insert(HitFlash {
            timer: HIT_FLASH_SECS,
        });
        // Frame do 1.º flash: clona o material, acende, original INTACTO.
        app.update();
        let world = app.world_mut();
        let original_after = world
            .resource::<Assets<StandardMaterial>>()
            .get(&original)
            .unwrap()
            .clone();
        assert_eq!(
            original_after.emissive,
            LinearRgba::BLACK,
            "asset partilhado não pode acender (todas as instâncias brilhariam)"
        );
        // O CLONE (agora no slot da entidade) está aceso no pico.
        {
            let mut q = world.query::<&MeshMaterial3d<StandardMaterial>>();
            let handle = q.get(world, enemy).unwrap().0.clone();
            let clone = world
                .resource::<Assets<StandardMaterial>>()
                .get(&handle)
                .unwrap();
            assert!(
                clone.emissive.red > 4.0,
                "flash aceso no clone: {:#?}",
                clone.emissive
            );
            assert!(handle != original, "slot aponta para o clone");
        }
        // Força a expiração (o TimePlugin do MinimalPlugins usa deltas reais
        // ~0 — escrever o timer a 0 é a via determinística no teste).
        world.entity_mut(enemy).insert(HitFlash { timer: 0.0 });
        app.update(); // remove + emissive a preto no frame de expiração

        let world = app.world_mut();
        assert!(
            world.get::<HitFlash>(enemy).is_none(),
            "flash removido no fim"
        );
        assert!(
            world.get::<FlashMaterials>(enemy).is_none(),
            "clone desanexado"
        );
        let mut q = world.query::<&MeshMaterial3d<StandardMaterial>>();
        let current = q.get(world, enemy).unwrap().0.clone();
        assert_eq!(
            current, original,
            "o slot VOLTA ao material original — o clone não fica (leak fechado)"
        );
        assert!(
            !world.resource::<Assets<StandardMaterial>>().contains(&current)
                || current == original,
            "sem clones órfãos no store"
        );
        assert_eq!(
            world.resource::<Assets<StandardMaterial>>().len(),
            baseline,
            "o clone foi removido — o store volta à linha de base do arranque"
        );
    }

    /// O hit-flash FECHA A CONTA: no fim do flash o nó volta ao material
    /// ORIGINAL e o clone sai do asset store. O comportamento anterior deixava
    /// o clone residente para sempre (o Bevy não faz GC de assets) — até
    /// `MAX_FLASH_MATS` materiais POR GOLPE a acumular numa sessão.
    #[test]
    fn test_hit_flash_remove_os_clones_no_fim() {
        let mut app = App::new();
        app.init_resource::<Time>()
            .init_resource::<Assets<StandardMaterial>>()
            .add_systems(Update, hit_flash_system);

        let (enemy, child, original) = {
            let world = app.world_mut();
            let original = world
                .resource_mut::<Assets<StandardMaterial>>()
                .add(StandardMaterial::default());
            let enemy = world
                .spawn(HitFlash {
                    timer: HIT_FLASH_SECS,
                })
                .id();
            let child = world
                .spawn((MeshMaterial3d(original.clone()), ChildOf(enemy)))
                .id();
            (enemy, child, original)
        };

        app.update(); // 1.º frame: clona e troca
        assert_eq!(
            app.world().resource::<Assets<StandardMaterial>>().len(),
            2,
            "o clone existe durante o flash"
        );

        for mut flash in app
            .world_mut()
            .query::<&mut HitFlash>()
            .iter_mut(app.world_mut())
        {
            flash.timer = 0.0;
        }
        app.update(); // fim: reverte e remove

        let world = app.world();
        assert_eq!(
            world.resource::<Assets<StandardMaterial>>().len(),
            1,
            "o clone foi removido do asset store"
        );
        assert_eq!(
            world.get::<MeshMaterial3d<StandardMaterial>>(child)
                .expect("o nó sobrevive ao flash")
                .0,
            original,
            "o nó voltou ao material ORIGINAL"
        );
        assert!(
            world.get_entity(enemy).is_ok(),
            "o inimigo sobrevive — só o flash sai"
        );
    }
}
